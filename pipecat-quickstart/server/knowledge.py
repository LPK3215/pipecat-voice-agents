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


# Measured with kb_eval.py on this repo's own documentation (15 hand-written cases):
# 300 chars was the worst size tested; 500-800 are clearly better and differ from each
# other by less than one case, so 500 (the most stable neighbourhood) is the default.
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 50

# Weight of the lexical signal on top of the vector score (0 = pure vector search).
# Measured: it improved **every** one of the 8 configurations tested, e.g. with
# size=500/overlap=50 it lifted hit@3 from 73% to 87% and hit@1 from 53% to 60%.
RERANK_ALPHA = 0.3
# Candidates pulled from the vector scan before reranking; must exceed k, otherwise
# reranking has nothing to reorder. Measured at 500/50 alpha=0.3: pool 10 -> 87%/0.71,
# 20 -> 80%/0.70, 40 -> 87%/0.73 (hit@3 / MRR), hence 40.
RERANK_POOL = 40


def chunk_text(
    text: str, size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP
) -> list[str]:
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
def ingest(
    text: str,
    source: str,
    title: str = "",
    embedder=None,
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> int:
    """Insert/overwrite a document (deduplicated by source). Returns the chunk count.

    ``size`` / ``overlap`` are exposed so the retrieval evaluation harness (``kb_eval.py``)
    can sweep them; normal callers should keep the defaults.
    """
    from embeddings import build_embedder

    emb = embedder or build_embedder()
    chunks = chunk_text(text, size=size, overlap=overlap)
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
def _bigrams(text: str) -> set[str]:
    """Character bigrams. Chinese has no spaces, so there are no words to match on."""
    cleaned = re.sub(r"[^\u4e00-\u9fff0-9a-zA-Z]", "", text or "")
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def _lexical_overlap(query: str, chunk: str) -> float:
    """Fraction of the query's bigrams that also appear in the chunk."""
    q = _bigrams(query)
    return len(q & _bigrams(chunk)) / len(q) if q else 0.0


def search(query: str, k: int = 3, embedder=None) -> list[dict]:
    """Semantic search: return the top-k chunks (with source and similarity score).

    The score blends the vector cosine with a lexical-overlap signal (``RERANK_ALPHA``):
    pure vector ranking misses passages that share the query's exact wording, which is
    common for short factual questions. See ``kb_eval.py`` for the measurements.

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

    pool = max(k, RERANK_POOL) if RERANK_ALPHA > 0 else k
    order = np.argsort(-sims)[: max(1, min(pool, len(rows)))]

    hits = []
    for i in order:
        text = rows[int(i)]["text"]
        cosine = float(sims[int(i)])
        score = (1 - RERANK_ALPHA) * cosine + RERANK_ALPHA * _lexical_overlap(q, text)
        hits.append(
            {
                "source": rows[int(i)]["source"],
                "title": rows[int(i)]["title"],
                "seq": rows[int(i)]["seq"],
                "score": round(score, 4),
                "text": text,
            }
        )
    hits.sort(key=lambda h: -h["score"])
    return hits[: max(1, min(k, len(hits)))]


def stats() -> dict:
    with _conn() as conn:
        docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    return {"documents": docs, "chunks": chunks}
