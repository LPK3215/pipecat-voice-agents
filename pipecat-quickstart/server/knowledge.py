"""本地知识库：文档切块 + 向量检索（RAG 的检索侧）。

定位与边界：
    这是"**非结构化文档**语义检索"——与 ``memory.py`` 的"**结构化数据**精确查询"
    互补。两者出口都是**一个工具**：
        query_data       查表（订单、指标、告警……）
        search_knowledge 查文档（手册、合同、知识文章……）

存储选型（与项目一贯的"能零依赖就零依赖"一致）：
    向量存 **SQLite BLOB**（float32），检索用 **NumPy 余弦**暴力扫描。
    几百 ~ 几千块文档规模下这是毫秒级且零新增依赖。
    数据量真的上来了要换向量库（sqlite-vec / FAISS / Chroma），
    **替换点只有本模块的 ``search()``**，上层工具与提示词都不用动。

嵌入后端由 ``embeddings.py`` 提供（默认本地 bge-small-zh，无需 key）。
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path

import numpy as np
from loguru import logger

# 默认与 memory 同目录，但独立文件 —— 知识库可以整体替换/清空而不动业务数据
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


# ---------------------------------------------------------------- 切块
# 中文没有空格，按「句子」切比按固定长度切更不容易切断语义
_SENT_RE = re.compile(r"[^。！？!?\n；;]+[。！？!?\n；;]?")


def chunk_text(text: str, size: int = 300, overlap: int = 50) -> list[str]:
    """把长文本切成带重叠的块。

    ``size`` 是目标字符数，``overlap`` 是相邻块的重叠 —— 重叠是为了避免
    「答案正好落在两块交界处」导致检索不到。
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


# ---------------------------------------------------------------- 写入
def ingest(text: str, source: str, title: str = "", embedder=None) -> int:
    """写入/覆盖一篇文档（按 source 去重）。返回切块数。"""
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
    logger.info(f"[KB] 已入库 {source}（{len(chunks)} 块）")
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


# ---------------------------------------------------------------- 检索
def search(query: str, k: int = 3, embedder=None) -> list[dict]:
    """语义检索：返回 top-k 块（含来源与相似度分数）。

    换向量库时**只改这个函数**（保持返回结构不变即可）。
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
    sims = mat @ qv  # 向量已归一化，点积即余弦
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
