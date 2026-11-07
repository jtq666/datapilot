import hashlib
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (name text PRIMARY KEY, password_hash text NOT NULL);
CREATE TABLE IF NOT EXISTS members (
    workspace text NOT NULL, username text REFERENCES users(name), can_publish boolean NOT NULL DEFAULT false,
    PRIMARY KEY (workspace, username)
);
CREATE TABLE IF NOT EXISTS sessions (
    digest text PRIMARY KEY, username text NOT NULL REFERENCES users(name), expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id text PRIMARY KEY, workspace text NOT NULL, owner text NOT NULL REFERENCES users(name),
    source text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS runs (
    id text PRIMARY KEY, workspace text NOT NULL, owner text NOT NULL REFERENCES users(name),
    source text NOT NULL, conversation text NOT NULL REFERENCES conversations(id), mode text NOT NULL,
    question text NOT NULL, recipient text, status text NOT NULL DEFAULT 'running',
    answer text, evidence jsonb, rule jsonb, deadline timestamptz, decision boolean,
    error text, created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE runs ADD COLUMN IF NOT EXISTS rule_key text;
CREATE INDEX IF NOT EXISTS runs_by_conversation ON runs(conversation,created_at,id);
CREATE TABLE IF NOT EXISTS events (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run text NOT NULL REFERENCES runs(id), event_key text NOT NULL, kind text NOT NULL,
    detail jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(run,event_key)
);
CREATE TABLE IF NOT EXISTS shares (
    run text PRIMARY KEY REFERENCES runs(id), recipient text NOT NULL REFERENCES users(name),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id text PRIMARY KEY, workspace text NOT NULL, source text NOT NULL, doc_key text NOT NULL,
    version integer NOT NULL, title text NOT NULL, active boolean NOT NULL,
    UNIQUE(workspace, source, doc_key, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_one_active
    ON knowledge_documents(workspace, source, doc_key) WHERE active;
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id text PRIMARY KEY, document text NOT NULL REFERENCES knowledge_documents(id),
    text text NOT NULL, embedding vector(1024) NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, dsn):
        self.dsn = dsn

    def connect(self, **kwargs):
        return psycopg.connect(self.dsn, connect_timeout=5, **kwargs)

    def setup(self):
        with self.connect() as conn:
            conn.execute(SCHEMA)

    def seed_user(self, username, password_hash, publisher):
        with self.connect() as conn:
            conn.execute("INSERT INTO users VALUES (%s,%s) ON CONFLICT (name) DO UPDATE SET password_hash=excluded.password_hash",
                         (username, password_hash))
            conn.execute("INSERT INTO members VALUES ('demo',%s,%s) ON CONFLICT (workspace,username) DO UPDATE SET can_publish=excluded.can_publish",
                         (username, publisher))

    def password(self, username):
        with self.connect() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE name=%s", (username,)).fetchone()
        return row[0] if row else None

    def issue_token(self, username):
        token = secrets.token_urlsafe(32)
        with self.connect() as conn:
            conn.execute("INSERT INTO sessions VALUES (%s,%s,%s)",
                         (self.digest(token), username, now() + timedelta(hours=8)))
        return token

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def authenticate(self, token):
        with self.connect() as conn:
            row = conn.execute("SELECT username FROM sessions WHERE digest=%s AND expires_at > now()",
                               (self.digest(token),)).fetchone()
        return row[0] if row else None

    def member(self, workspace, user, publisher=False):
        with self.connect() as conn:
            return bool(conn.execute(
                "SELECT 1 FROM members WHERE workspace=%s AND username=%s AND (can_publish OR NOT %s)",
                (workspace, user, publisher),
            ).fetchone())

    def recipients(self, workspace, user):
        with self.connect() as conn:
            return [r[0] for r in conn.execute(
                "SELECT username FROM members WHERE workspace=%s AND username<>%s ORDER BY username", (workspace, user)
            )]

    def create_run(self, data, conversation):
        with self.connect() as conn:
            if conversation:
                exists = conn.execute("SELECT 1 FROM conversations WHERE id=%s AND workspace=%s AND owner=%s AND source=%s",
                                      (conversation, data["workspace"], data["owner"], data["source"])).fetchone()
                if not exists:
                    raise LookupError("conversation not found")
            else:
                conversation = data["id"]
                conn.execute("INSERT INTO conversations(id,workspace,owner,source) VALUES (%s,%s,%s,%s)",
                             (conversation, data["workspace"], data["owner"], data["source"]))
            conn.execute("""
                INSERT INTO runs(id,workspace,owner,source,mode,question,recipient,conversation,rule_key)
                VALUES (%(id)s,%(workspace)s,%(owner)s,%(source)s,%(mode)s,%(question)s,%(recipient)s,%(conversation)s,%(rule_key)s)
            """, {"rule_key": None, **data, "conversation": conversation})
        return conversation

    def history(self, conversation, exclude):
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT question, answer FROM runs WHERE conversation=%s AND id<>%s AND answer IS NOT NULL
                ORDER BY created_at DESC, id DESC LIMIT 5
            """, (conversation, exclude)).fetchall()
        return [message for q, a in reversed(rows) for message in (
            {"role": "user", "content": q}, {"role": "assistant", "content": a}
        )]

    def run(self, run_id, workspace, owner):
        with self.connect(row_factory=dict_row) as conn:
            # Compute expiry on read so pending tasks are bounded without a worker.
            return conn.execute("""
                SELECT id,workspace,owner,source,conversation,mode,question,recipient,answer,evidence,rule,deadline,
                    decision,error,created_at,rule_key,
                    CASE WHEN status='waiting' AND decision IS NULL AND deadline<=now()
                         THEN 'expired' ELSE status END AS status
                FROM runs WHERE id=%s AND workspace=%s AND owner=%s
            """, (run_id, workspace, owner)).fetchone()

    def runs(self, workspace, owner):
        with self.connect(row_factory=dict_row) as conn:
            return conn.execute("""
                SELECT id, question, conversation, created_at, CASE WHEN status='waiting' AND decision IS NULL AND deadline<=now()
                    THEN 'expired' ELSE status END AS status
                FROM runs WHERE workspace=%s AND owner=%s ORDER BY created_at DESC LIMIT 50
            """, (workspace, owner)).fetchall()

    def conversations(self, workspace, owner):
        with self.connect(row_factory=dict_row) as conn:
            return conn.execute("""
                SELECT c.id, c.source, first.question AS title, latest.created_at AS updated_at
                FROM conversations c
                JOIN LATERAL (SELECT question FROM runs WHERE conversation=c.id
                              ORDER BY created_at,id LIMIT 1) first ON true
                JOIN LATERAL (SELECT created_at FROM runs WHERE conversation=c.id
                              ORDER BY created_at DESC,id DESC LIMIT 1) latest ON true
                WHERE c.workspace=%s AND c.owner=%s ORDER BY latest.created_at DESC LIMIT 50
            """, (workspace, owner)).fetchall()

    def turns(self, conversation, workspace, owner):
        with self.connect(row_factory=dict_row) as conn:
            exists = conn.execute(
                "SELECT 1 FROM conversations WHERE id=%s AND workspace=%s AND owner=%s",
                (conversation, workspace, owner),
            ).fetchone()
            if not exists:
                return None
            return conn.execute("""
                SELECT * FROM (
                    SELECT id,conversation,mode,question,recipient,answer,evidence,rule,deadline,decision,error,created_at,
                           CASE WHEN status='waiting' AND decision IS NULL AND deadline<=now()
                           THEN 'expired' ELSE status END AS status
                    FROM runs WHERE conversation=%s ORDER BY created_at DESC,id DESC LIMIT 50
                ) latest ORDER BY created_at,id
            """, (conversation,)).fetchall()

    def event(self, run_id, key, kind, detail):
        with self.connect() as conn:
            conn.execute("INSERT INTO events(run,event_key,kind,detail) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                         (run_id, key, kind, Jsonb(detail)))

    def events(self, run_id):
        with self.connect(row_factory=dict_row) as conn:
            return conn.execute("""
                SELECT kind,detail,created_at FROM events WHERE run=%s ORDER BY sequence
            """, (run_id,)).fetchall()

    def report(self, run_id, answer, evidence, rule, approval):
        status = "waiting" if approval else "completed"
        deadline = now() + timedelta(hours=24) if approval else None
        with self.connect() as conn:
            conn.execute("""
                UPDATE runs SET answer=%s,evidence=%s,rule=%s,status=%s,deadline=COALESCE(deadline,%s)
                WHERE id=%s AND status='running'
            """, (answer, Jsonb(evidence), Jsonb(rule), status, deadline, run_id))

    def finish(self, run_id, status, error=None):
        with self.connect() as conn:
            conn.execute("UPDATE runs SET status=%s,error=%s WHERE id=%s AND status IN ('running','waiting')",
                         (status, error, run_id))

    @contextmanager
    def approval_lock(self, run_id):
        # Keep the session open throughout resume to serialize across API workers.
        with self.connect(autocommit=True) as conn:
            acquired, = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (run_id,)).fetchone()
            yield acquired

    def decide(self, run_id, approval):
        with self.connect() as conn:
            return conn.execute("""
                UPDATE runs SET decision=%s WHERE id=%s AND status='waiting' AND decision IS NULL AND deadline>now()
                RETURNING id
            """, (approval, run_id)).fetchone() is not None

    def share(self, run_id):
        with self.connect() as conn:
            row = conn.execute("""
                INSERT INTO shares(run,recipient)
                SELECT r.id,r.recipient FROM runs r
                JOIN members m ON m.workspace=r.workspace AND m.username=r.owner
                JOIN members p ON p.workspace=r.workspace AND p.username=r.recipient
                WHERE r.id=%s AND r.status IN ('waiting','shared') AND r.decision=true
                ON CONFLICT(run) DO UPDATE SET recipient=excluded.recipient RETURNING run
            """, (run_id,)).fetchone()
            if row:
                conn.execute("UPDATE runs SET status='shared' WHERE id=%s", (run_id,))
        return row is not None

    def inbox(self, workspace, user):
        with self.connect(row_factory=dict_row) as conn:
            return conn.execute("""
                SELECT r.id,r.question,r.owner,s.created_at FROM shares s JOIN runs r ON r.id=s.run
                WHERE r.workspace=%s AND s.recipient=%s ORDER BY s.created_at DESC LIMIT 50
            """, (workspace, user)).fetchall()

    def shared_report(self, workspace, user, run_id):
        with self.connect(row_factory=dict_row) as conn:
            return conn.execute("""
                SELECT r.id,r.question,r.owner,r.answer,r.evidence,r.rule,s.created_at
                FROM shares s JOIN runs r ON r.id=s.run
                WHERE r.workspace=%s AND s.recipient=%s AND r.id=%s
            """, (workspace, user, run_id)).fetchone()
