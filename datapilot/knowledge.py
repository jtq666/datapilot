import math
import re
from collections import Counter
from uuid import uuid4

from psycopg.rows import dict_row


MAX_CHUNKS = 5000


def split_document(text: str):
    # Fixed character windows keep demo documents easy to inspect.
    text = text.strip()
    return [text[start:start + 650] for start in range(0, len(text), 550)
            if start == 0 or start + 100 < len(text)]


def tokenize(text: str):
    for token in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", text.lower()):
        if token.isascii() or len(token) == 1:
            yield token
        else:
            yield from (token[i:i + 2] for i in range(len(token) - 1))


def bm25(rows, question):
    if not rows:
        return []
    terms = set(tokenize(question))
    counts = [Counter(tokenize(row["text"])) for row in rows]
    lengths = [sum(count.values()) for count in counts]
    average = sum(lengths) / len(rows) or 1
    frequency = {term: sum(term in count for count in counts) for term in terms}
    ranked = []
    for row, count, length in zip(rows, counts, lengths):
        score = 0.0
        for term in terms & count.keys():
            matches = count[term]
            df = frequency[term]
            idf = math.log1p((len(rows) - df + 0.5) / (df + 0.5))
            score += idf * matches * 2.2 / (matches + 1.2 * (0.25 + 0.75 * length / average))
        if score:
            ranked.append((score, row["chunk_id"]))
    return [chunk_id for _, chunk_id in sorted(ranked, key=lambda item: (-item[0], item[1]))[:10]]


def fuse(*rankings):
    scores = Counter()
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, 1):
            scores[chunk_id] += 1 / (60 + rank)
    return sorted(scores, key=lambda key: (-scores[key], key))[:5]


def vector_literal(vector):
    if len(vector) != 1024 or not any(vector) or any(not math.isfinite(x) for x in vector):
        raise ValueError("invalid embedding")
    return "[" + ",".join(str(x) for x in vector) + "]"


class Knowledge:
    def __init__(self, store, embeddings):
        self.store = store
        self.embeddings = embeddings

    def publish(self, workspace, source, key, title, text):
        chunks = split_document(text)
        if not chunks:
            raise ValueError("empty document")
        vectors = [vector_literal(v) for v in self.embeddings.passages(chunks)]
        if len(vectors) != len(chunks):
            raise ValueError("embedding count mismatch")
        with self.store.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 1))", (f"{workspace}:{source}",))
            version, = conn.execute("""
                SELECT COALESCE(MAX(version), 0) + 1 FROM knowledge_documents
                WHERE workspace=%s AND source=%s AND doc_key=%s
            """, (workspace, source, key)).fetchone()
            count, = conn.execute("""
                SELECT count(*) FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document
                WHERE d.workspace=%s AND d.source=%s AND d.active AND d.doc_key<>%s
            """, (workspace, source, key)).fetchone()
            if count + len(chunks) > MAX_CHUNKS:
                raise ValueError("active knowledge exceeds the demo corpus limit")
            doc = uuid4().hex
            conn.execute("UPDATE knowledge_documents SET active=false WHERE workspace=%s AND source=%s AND doc_key=%s AND active",
                         (workspace, source, key))
            conn.execute("INSERT INTO knowledge_documents VALUES (%s,%s,%s,%s,%s,%s,true)",
                         (doc, workspace, source, key, version, title))
            with conn.cursor() as cursor:
                cursor.executemany("INSERT INTO knowledge_chunks VALUES (%s,%s,%s,%s::vector)",
                                   [(f"{doc}:{i}", doc, chunk, vector)
                                    for i, (chunk, vector) in enumerate(zip(chunks, vectors))])
        return {"key": key, "version": version, "chunks": len(chunks)}

    def documents(self, workspace, source):
        with self.store.connect(row_factory=dict_row) as conn:
            return conn.execute("""
                SELECT doc_key AS key, title, version FROM knowledge_documents
                WHERE workspace=%s AND source=%s AND active ORDER BY doc_key
            """, (workspace, source)).fetchall()

    def document(self, workspace, source, key, version):
        with self.store.connect(row_factory=dict_row) as conn:
            versions = conn.execute("""
                SELECT id, title, version, active FROM knowledge_documents
                WHERE workspace=%s AND source=%s AND doc_key=%s ORDER BY version DESC
            """, (workspace, source, key)).fetchall()
            selected = next((row for row in versions if
                             (row["version"] == version if version is not None else row["active"])), None)
            if selected is None:
                return None
            chunks = conn.execute("""
                SELECT id, text FROM knowledge_chunks WHERE document=%s
                ORDER BY split_part(id, ':', 2)::integer
            """, (selected["id"],)).fetchall()
        return {"key": key, "title": selected["title"], "version": selected["version"], "chunks": chunks,
                "text": "".join(chunk["text"] if i == 0 else chunk["text"][100:]
                                for i, chunk in enumerate(chunks)),
                "versions": [{"version": row["version"], "active": row["active"]} for row in versions]}

    def search(self, workspace, source, question, rule_key=None):
        vector = vector_literal(self.embeddings.queries([question])[0])
        with self.store.connect(row_factory=dict_row) as conn:
            # Both recall branches and the selected rule read the same publication snapshot.
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            base = """
                FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document
                WHERE d.workspace=%s AND d.source=%s AND d.active
            """
            columns = "SELECT c.id AS chunk_id, c.text, d.doc_key AS key, d.version, d.title "
            scope = (workspace, source)
            rows = conn.execute(columns + base + " ORDER BY c.id", scope).fetchall()
            vector_hits = conn.execute(
                "SELECT c.id " + base + " AND (%s::text IS NULL OR d.doc_key<>%s)"
                " ORDER BY c.embedding <=> %s::vector, c.id LIMIT 10",
                (*scope, rule_key, rule_key, vector),
            ).fetchall()
        rule = [row for row in rows if row["key"] == rule_key] if rule_key else []
        by_id = {row["chunk_id"]: row for row in rows if row["key"] != rule_key}
        keyword_ids = bm25(list(by_id.values()), question)
        vector_ids = [row["id"] for row in vector_hits]
        fused = fuse(keyword_ids, vector_ids)
        return {"rule": rule, "matches": [by_id[key] for key in fused],
                "keyword_ids": keyword_ids, "vector_ids": vector_ids}
