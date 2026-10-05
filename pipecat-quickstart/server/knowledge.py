"""Local knowledge base: document chunking + vector retrieval (the retrieval side of RAG).

Scope and boundaries:
    This is semantic retrieval over **unstructured documents** -- complementary to
    ``memory.py``'s exact querying of **structured data**. Both are exposed as **one tool**:
        query_data        query tables (orders, metrics, alerts, ...)
        search_knowledge  query documents (manuals, contracts, articles, ...)

Storage choice (consistent with the project's "zero deps when possible" stance):
    Vectors are stored as **SQLite BLOBs** (float32) and retrieval is a brute-force
    **NumPy cosine** scan. At a few hundred to a few thousand chunks this is millisecond
    scale with no new dependencies. If volume really grows, swap in a vector store
    (sqlite-vec / FAISS / Chroma); the **only replacement point is ``search()``** in this
    module, so upper-layer tools and prompts stay untouched.

The embedding backend is provided by ``embeddings.py`` (default local bge-small-zh, no key).
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path

import numpy as np
from loguru import logger

# Same directory as memory by default, but a separate file -- the knowledge base can be
# swapped or cleared wholesale without touching business data.
DB_PATH = Path(
    os.getenv("KNOWLEDGE_DB", str(Path(__file__).resolve().parent / "data" / "knowledge.db"))
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    source     TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    chunks     INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    seq        INTEGER NOT NULL DEFAULT 0,
    text       TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vec        BLOB NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source, seq);
CREATE INDEX IF NOT EXISTS idx_chunks_dim ON chunks(dim);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------- chunking
# Chinese has no spaces, so splitting on "sentences" breaks semantics less often than
# splitting on a fixed length. NOTE: this pattern intentionally matches Chinese punctuation.
_SENT_RE = re.compile(r"[^。！？!?\n；;]+[。！？!?\n；;]?")


def chunk_text(text: str, size: int = 300, overlap: int = 50) -> list[str]:
    """Split long text into overlapping chunks.

    ``size`` is the target character count and ``overlap`` is the overlap between adjacent
    chunks -- the overlap avoids "the answer sits exactly on a chunk boundary" misses.
    """
    text = (text or "").strip()
    if not text:
        return []
    sents = [s.strip() for s in _SENT_RE.findall(text) if s.strip()]

    chunks: list[str] = []
    cur = ""
    for s in sents:
        if cur and len(cur) + len(s) > size:
            chunks.append(cur)
            tail = cur[-overlap:] if overlap > 0 else ""
            cur = tail + s
        else:
            cur += s
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


# ---------------------------------------------------------------- writes
def ingest(text: str, source: str, title: str = "", embedder=None) -> int:
    """Insert/overwrite a document (deduplicated by source). Returns the chunk count."""
    from embeddings import build_embedder

    emb = embedder or build_embedder()
    chunks = chunk_text(text)
    if not chunks:
        return 0
    vecs = emb.embed(chunks)
    now = time.time()

    with _conn() as conn:
        conn.execute("DELETE FROM chunks WHERE source=?", (source,))
        conn.execute(
            """INSERT INTO documents (source, title, chunks, created_at, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET
                   title=excluded.title, chunks=excluded.chunks, updated_at=excluded.updated_at""",
            (source, title, len(chunks), now, now),
        )
        conn.executemany(
            "INSERT INTO chunks (source, title, seq, text, dim, vec, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (source, title, i, ch, int(v.shape[0]), v.astype("float32").tobytes(), now)
                for i, (ch, v) in enumerate(zip(chunks, vecs))
            ],
        )
    logger.info(f"[KB] ingested {source} ({len(chunks)} chunks)")
    return len(chunks)


def delete_document(source: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM chunks WHERE source=?", (source,))
        conn.execute("DELETE FROM documents WHERE source=?", (source,))
    return cur.rowcount > 0


def list_documents() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT source, title, chunks, updated_at FROM documents ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- retrieval
def search(query: str, k: int = 3, embedder=None) -> list[dict]:
    """Semantic search: return the top-k chunks (with source and similarity score).

    Swapping the vector store means **changing only this function** (keep the return shape).
    """
    from embeddings import build_embedder

    q = (query or "").strip()
    if not q:
        return []
    emb = embedder or build_embedder()
    qv = emb.embed([q])[0]
    dim = int(qv.shape[0])

    with _conn() as conn:
        rows = conn.execute(
            "SELECT source, title, seq, text, vec FROM chunks WHERE dim=?", (dim,)
        ).fetchall()
    if not rows:
        return []

    mat = np.stack([np.frombuffer(r["vec"], dtype="float32") for r in rows])
    sims = mat @ qv  # vectors are normalized, so the dot product is the cosine
    order = np.argsort(-sims)[: max(1, min(k, len(rows)))]
    return [
        {
            "source": rows[int(i)]["source"],
            "title": rows[int(i)]["title"],
            "seq": rows[int(i)]["seq"],
            "score": round(float(sims[int(i)]), 4),
            "text": rows[int(i)]["text"],
        }
        for i in order
    ]


def stats() -> dict:
    with _conn() as conn:
        docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    return {"documents": docs, "chunks": chunks}
