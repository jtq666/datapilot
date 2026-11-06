from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from uuid import uuid4

from .chunking import search_tokens, split_text
from .contracts import EmbeddingProvider, SearchHit


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return -1.0
    denominator = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / denominator if denominator else -1.0


class KnowledgeStore:
    """SQLite development index; large/production indexes need a separate adapter."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_bases (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, name TEXT NOT NULL,
                    embedding_model TEXT, embedding_dim INTEGER,
                    UNIQUE(workspace_id, name)
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES knowledge_bases(id),
                    filename TEXT NOT NULL, sha256 TEXT NOT NULL,
                    UNIQUE(kb_id, sha256)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    ordinal INTEGER NOT NULL, text TEXT NOT NULL, embedding TEXT NOT NULL,
                    UNIQUE(document_id, ordinal)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                    chunk_id UNINDEXED, tokens
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_bases)")}
            if "embedding_model" not in columns:
                db.execute("ALTER TABLE knowledge_bases ADD COLUMN embedding_model TEXT")
            if "embedding_dim" not in columns:
                db.execute("ALTER TABLE knowledge_bases ADD COLUMN embedding_dim INTEGER")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def create_base(self, workspace_id: str, name: str) -> str:
        if not workspace_id or not name.strip():
            raise ValueError("workspace and knowledge base name are required")
        kb_id = str(uuid4())
        with self._connect() as db:
            db.execute(
                "INSERT INTO knowledge_bases(id, workspace_id, name) VALUES (?, ?, ?)",
                (kb_id, workspace_id, name.strip()),
            )
        return kb_id

    def _check_scope(self, db: sqlite3.Connection, workspace_id: str, kb_id: str) -> None:
        if db.execute("SELECT 1 FROM knowledge_bases WHERE id=? AND workspace_id=?", (kb_id, workspace_id)).fetchone() is None:
            raise LookupError("knowledge base not found in workspace")

    def ensure_scope(self, workspace_id: str, kb_id: str) -> None:
        with self._connect() as db:
            self._check_scope(db, workspace_id, kb_id)

    def list_bases(self, workspace_id: str) -> list[dict[str, str]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, name FROM knowledge_bases WHERE workspace_id=? ORDER BY name",
                (workspace_id,),
            ).fetchall()
        return [{"id": row[0], "name": row[1]} for row in rows]

    def add_document(
        self, workspace_id: str, kb_id: str, filename: str, text: str,
        embeddings: EmbeddingProvider, *, chunk_size: int = 1000, overlap: int = 150,
    ) -> str:
        if not filename or not filename.lower().endswith((".txt", ".md")):
            raise ValueError("only .txt and .md documents are supported for now")
        if len(text.encode("utf-8")) > 2_000_000:
            raise ValueError("document exceeds 2 MB limit")
        pieces = split_text(text, size=chunk_size, overlap=overlap)
        if not pieces:
            raise ValueError("document has no searchable text")
        model_name = str(getattr(embeddings, "model", type(embeddings).__name__))
        with self._connect() as db:
            self._check_scope(db, workspace_id, kb_id)
            model_row = db.execute(
                "SELECT embedding_model FROM knowledge_bases WHERE id=?", (kb_id,)
            ).fetchone()
            if model_row[0] is not None and model_row[0] != model_name:
                raise ValueError("embedding model differs from knowledge base index")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            existing = db.execute("SELECT id FROM documents WHERE kb_id=? AND sha256=?", (kb_id, digest)).fetchone()
            if existing:
                return existing[0]
        vectors = embeddings.passages(pieces)
        dimensions = {len(vector) for vector in vectors}
        if len(vectors) != len(pieces) or len(dimensions) != 1 or 0 in dimensions:
            raise ValueError("embedding provider returned inconsistent vectors")
        if any(not math.isfinite(value) for vector in vectors for value in vector):
            raise ValueError("embedding provider returned a non-finite value")
        dimension = dimensions.pop()
        document_id = str(uuid4())
        with self._connect() as db:
            self._check_scope(db, workspace_id, kb_id)
            model_row = db.execute(
                "SELECT embedding_model, embedding_dim FROM knowledge_bases WHERE id=?", (kb_id,)
            ).fetchone()
            if model_row[0] is not None and model_row != (model_name, dimension):
                raise ValueError("embedding model or dimensions differ from knowledge base index")
            db.execute(
                "UPDATE knowledge_bases SET embedding_model=?, embedding_dim=? WHERE id=?",
                (model_name, dimension, kb_id),
            )
            db.execute("INSERT INTO documents VALUES (?, ?, ?, ?)", (document_id, kb_id, filename, digest))
            for ordinal, (piece, vector) in enumerate(zip(pieces, vectors)):
                chunk_id = str(uuid4())
                db.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                           (chunk_id, document_id, ordinal, piece, json.dumps(vector)))
                db.execute("INSERT INTO chunk_fts(chunk_id, tokens) VALUES (?, ?)",
                           (chunk_id, " ".join(search_tokens(piece))))
        return document_id

    def delete_document(self, workspace_id: str, kb_id: str, document_id: str) -> None:
        with self._connect() as db:
            self._check_scope(db, workspace_id, kb_id)
            chunk_ids = [row[0] for row in db.execute(
                "SELECT c.id FROM chunks c JOIN documents d ON d.id=c.document_id "
                "WHERE d.id=? AND d.kb_id=?", (document_id, kb_id)
            )]
            if not chunk_ids:
                raise LookupError("document not found in knowledge base")
            for chunk_id in chunk_ids:
                db.execute("DELETE FROM chunk_fts WHERE chunk_id=?", (chunk_id,))
            db.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
            db.execute("DELETE FROM documents WHERE id=? AND kb_id=?", (document_id, kb_id))

    def list_documents(self, workspace_id: str, kb_id: str) -> list[dict[str, str | int]]:
        with self._connect() as db:
            self._check_scope(db, workspace_id, kb_id)
            rows = db.execute(
                "SELECT d.id, d.filename, COUNT(c.id) FROM documents d "
                "LEFT JOIN chunks c ON c.document_id=d.id WHERE d.kb_id=? "
                "GROUP BY d.id ORDER BY d.filename", (kb_id,),
            ).fetchall()
        return [{"id": row[0], "filename": row[1], "chunks": row[2]} for row in rows]

    def list_chunks(self, workspace_id: str, kb_id: str, document_id: str | None = None) -> list[dict]:
        """Return indexed text blocks for inspection and index maintenance APIs."""
        with self._connect() as db:
            self._check_scope(db, workspace_id, kb_id)
            query = (
                "SELECT c.id, c.document_id, d.filename, c.ordinal, c.text "
                "FROM chunks c JOIN documents d ON d.id=c.document_id "
                "WHERE d.kb_id=?"
            )
            args: list[str] = [kb_id]
            if document_id:
                query += " AND c.document_id=?"
                args.append(document_id)
            query += " ORDER BY d.filename, c.ordinal"
            rows = db.execute(query, args).fetchall()
        return [dict(zip(("id", "document_id", "filename", "ordinal", "text"), row)) for row in rows]

    def get_chunk(self, workspace_id: str, kb_id: str, chunk_id: str) -> dict | None:
        with self._connect() as db:
            self._check_scope(db, workspace_id, kb_id)
            row = db.execute(
                "SELECT c.id, c.document_id, d.filename, c.ordinal, c.text "
                "FROM chunks c JOIN documents d ON d.id=c.document_id "
                "WHERE c.id=? AND d.kb_id=?", (chunk_id, kb_id),
            ).fetchone()
        return None if row is None else dict(zip(("id", "document_id", "filename", "ordinal", "text"), row))

    def update_chunk(self, workspace_id: str, kb_id: str, chunk_id: str,
                     text: str, embeddings: EmbeddingProvider) -> dict:
        if not text.strip() or len(text) > 100_000:
            raise ValueError("chunk text must be nonempty and at most 100000 characters")
        with self._connect() as db:
            self._check_scope(db, workspace_id, kb_id)
            row = db.execute(
                "SELECT c.document_id, c.ordinal FROM chunks c JOIN documents d ON d.id=c.document_id "
                "WHERE c.id=? AND d.kb_id=?", (chunk_id, kb_id),
            ).fetchone()
            model_row = db.execute(
                "SELECT embedding_model, embedding_dim FROM knowledge_bases WHERE id=?", (kb_id,)
            ).fetchone()
        if row is None:
            raise LookupError("chunk not found")
        vectors = embeddings.passages([text])
        if len(vectors) != 1 or not vectors[0]:
            raise ValueError("embedding provider returned no vector")
        model_name = str(getattr(embeddings, "model", type(embeddings).__name__))
        if model_row[0] is not None and model_row != (model_name, len(vectors[0])):
            raise ValueError("embedding model or dimensions differ from knowledge base index")
        with self._connect() as db:
            db.execute("UPDATE chunks SET text=?, embedding=? WHERE id=?",
                       (text, json.dumps(vectors[0]), chunk_id))
            db.execute("UPDATE chunk_fts SET tokens=? WHERE chunk_id=?",
                       (" ".join(search_tokens(text)), chunk_id))
        return self.get_chunk(workspace_id, kb_id, chunk_id) or {}

    def search(
        self, workspace_id: str, kb_id: str, query: str, embeddings: EmbeddingProvider,
        *, top_k: int = 5, candidate_k: int = 20,
    ) -> list[SearchHit]:
        if not query.strip() or not 1 <= top_k <= 20 or not top_k <= candidate_k <= 100:
            raise ValueError("invalid query or retrieval limits")
        tokens = list(dict.fromkeys(search_tokens(query)))
        with self._connect() as db:
            self._check_scope(db, workspace_id, kb_id)
            model_row = db.execute(
                "SELECT embedding_model, embedding_dim FROM knowledge_bases WHERE id=?", (kb_id,)
            ).fetchone()
            if not tokens:
                return []
            match = " OR ".join('"' + token + '"' for token in tokens)
            keyword_ids = [row[0] for row in db.execute(
                "SELECT f.chunk_id FROM chunk_fts f "
                "JOIN chunks c ON c.id=f.chunk_id JOIN documents d ON d.id=c.document_id "
                "WHERE d.kb_id=? AND chunk_fts MATCH ? ORDER BY bm25(chunk_fts) LIMIT ?",
                (kb_id, match, candidate_k),
            ).fetchall()]
            records = db.execute(
                "SELECT c.id, c.document_id, d.filename, c.ordinal, c.text, c.embedding "
                "FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.kb_id=? LIMIT 10001",
                (kb_id,),
            ).fetchall()
        if len(records) > 10000:
            raise ValueError("development vector index exceeds 10000 chunks")
        if not records:
            return []
        vectors = embeddings.queries([query])
        if len(vectors) != 1 or not vectors[0]:
            raise ValueError("embedding provider returned no query vector")
        query_vector = vectors[0]
        model_name = str(getattr(embeddings, "model", type(embeddings).__name__))
        if model_row[0] is not None and model_row != (model_name, len(query_vector)):
            raise ValueError("embedding model or dimensions differ from knowledge base index")
        vector_ids = [row[0] for row in sorted(
            records, key=lambda row: _cosine(query_vector, json.loads(row[5])), reverse=True
        )[:candidate_k]]
        keyword_rank = {chunk_id: rank for rank, chunk_id in enumerate(keyword_ids, 1)}
        vector_rank = {chunk_id: rank for rank, chunk_id in enumerate(vector_ids, 1)}
        candidates = set(keyword_rank) | set(vector_rank)
        by_id = {row[0]: row for row in records}
        ranked = sorted(candidates, key=lambda chunk_id: (
            -(1 / (60 + keyword_rank[chunk_id]) if chunk_id in keyword_rank else 0)
            - (1 / (60 + vector_rank[chunk_id]) if chunk_id in vector_rank else 0),
            chunk_id,
        ))
        return [SearchHit(
            chunk_id=chunk_id, document_id=by_id[chunk_id][1], filename=by_id[chunk_id][2],
            ordinal=by_id[chunk_id][3], text=by_id[chunk_id][4],
            score=(1 / (60 + keyword_rank[chunk_id]) if chunk_id in keyword_rank else 0)
                  + (1 / (60 + vector_rank[chunk_id]) if chunk_id in vector_rank else 0),
            keyword_rank=keyword_rank.get(chunk_id), vector_rank=vector_rank.get(chunk_id),
        ) for chunk_id in ranked[:top_k]]

