#!/usr/bin/env python3
"""Retrieval-quality evaluation for the local knowledge base (the RAG tuning harness).

Why this exists:
    `knowledge.py` ships a *minimum viable* retrieval: sentence-aware chunking with
    configurable size/overlap, L2-normalized embeddings, and a brute-force cosine scan.
    "Tune the chunking / add a reranker" is only meaningful if there is something to
    measure -- this script provides it, using the repository's own Chinese documentation
    as the corpus (real prose, not synthetic filler).

How the ground truth works:
    Each case is (question, keyword). A case counts as a HIT when the retrieved chunk
    **contains the keyword** -- i.e. the passage that actually answers the question was
    retrieved. Keywords are chosen to appear in as few chunks as possible, so "hit" is not
    a free pass.

    This is a small, hand-written set (~15 cases) over one corpus. It gives a directional
    signal for chunking and reranking; it is not a benchmark. Treat differences of one case
    as noise.

What it measures, per configuration:
    hit@1 / hit@3 / MRR (mean reciprocal rank), and the chunk count (index size).

What it compares:
    chunk size x overlap, and a dependency-free lexical reranker on top of the vector
    score:  final = (1 - alpha) * cosine + alpha * bigram_overlap(query, chunk).
    Character bigrams are used because Chinese has no spaces.

Usage:
    cd server && uv run ../kb_eval.py                    # default corpus = repo docs
    cd server && uv run ../kb_eval.py --top-k 5
    cd server && uv run ../kb_eval.py --doc ../README.md
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
SERVER = BASE / "server"

# The knowledge base reads KNOWLEDGE_DB at import time -- point it at a scratch DB before
# importing anything, so the real server/data/knowledge.db is never touched.
SCRATCH_DB = BASE / ".cache" / "kb_eval.db"
os.environ["KNOWLEDGE_DB"] = str(SCRATCH_DB)
sys.path.insert(0, str(SERVER))

DEFAULT_DOCS = ["README.md", "HANDBOOK.md", "HANDBOOK-02.md", "TOOL_TESTS.md"]

# (question, keyword that must appear in the retrieved chunk)
# NOTE: the Chinese strings here are evaluation data -- do not translate.
CASES: list[tuple[str, str]] = [
    ("VAD 的 stop_secs 默认设成多少，为什么", "0.6"),
    ("SenseVoice 的中文识别字错率是多少", "10.2"),
    ("SenseVoice 单句识别要多久", "158"),
    ("怎么安装 SenseVoice 需要的依赖", "--extra sensevoice"),
    ("知识库里默认用的嵌入模型是哪个", "bge-small-zh"),
    ("上下文摘要超过多少条消息会触发压缩", "20 条"),
    ("远程访问时听不到声音是为什么", "TURN"),
    ("怎么切换 LLM 服务商", "LLM_PROVIDER"),
    ("关闭思考模式后首 token 快了多少", "3.6 倍"),
    ("工具变多会不会降低调用可靠性", "不存在随工具数量"),
    ("谎报执行是什么，怎么处理", "谎报执行"),
    ("多步任务为什么要用显式编排", "北京"),
    ("采集与查询为什么要分成两步", "外部数据源"),
    ("工具的 result_callback 必须传什么", "run_llm"),
    ("知识库检索和结构化查询怎么分工", "search_knowledge"),
]

DEFAULT_SIZES = [(300, 0), (300, 50), (500, 50)]


def evaluate(rows: list[tuple[str, str]], top_k: int, alpha: float, pool: int) -> dict:
    """Score the product as-is: the lexical reranker lives in knowledge.py, so sweep its
    weight (``RERANK_ALPHA``) and candidate pool (``RERANK_POOL``) instead of
    re-implementing the blend here."""
    import knowledge

    knowledge.RERANK_ALPHA = alpha
    knowledge.RERANK_POOL = pool

    hit1 = hitk = 0
    rr = 0.0
    misses: list[str] = []
    for question, keyword in rows:
        hits = knowledge.search(question, k=max(top_k, 10))
        rank = next((i for i, h in enumerate(hits, 1) if keyword in h["text"]), 0)
        if rank == 1:
            hit1 += 1
        if 0 < rank <= top_k:
            hitk += 1
        if rank:
            rr += 1 / rank
        else:
            misses.append(f"{question}  [需包含 {keyword!r}]")
    n = max(1, len(rows))
    return {
        "hit@1": hit1 / n,
        "hit@k": hitk / n,
        "mrr": rr / n,
        "misses": misses,
    }


def build_index(docs: list[Path], size: int, overlap: int) -> int:
    import knowledge

    SCRATCH_DB.unlink(missing_ok=True)
    knowledge.init_db()
    chunks = 0
    for path in docs:
        text = path.read_text(encoding="utf-8", errors="replace")
        chunks += knowledge.ingest(text, source=path.name, title=path.stem, size=size, overlap=overlap)
    return chunks


def main() -> int:
    ap = argparse.ArgumentParser(description="knowledge base retrieval evaluation")
    ap.add_argument("--doc", action="append", default=None, help="extra corpus file (repeatable)")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--alpha", type=float, default=None, help="fix alpha instead of sweeping")
    ap.add_argument("--pool", type=int, default=None, help="fix the rerank candidate pool")
    ap.add_argument("--sizes", default=None, help="comma-separated chunk sizes, e.g. 300,500")
    ap.add_argument("--overlaps", default=None, help="comma-separated overlaps, e.g. 0,50")
    args = ap.parse_args()

    sizes = [int(s) for s in args.sizes.split(",")] if args.sizes else None
    overlaps = [int(o) for o in args.overlaps.split(",")] if args.overlaps else None
    configs = (
        [(s, o) for s in sizes for o in overlaps]
        if sizes and overlaps
        else DEFAULT_SIZES
    )

    docs = [Path(d) if Path(d).is_absolute() else BASE / d for d in DEFAULT_DOCS]
    docs += [Path(d) for d in (args.doc or [])]
    missing = [d for d in docs if not d.exists()]
    if missing:
        print("corpus file not found:", ", ".join(str(m) for m in missing))
        return 1

    print("=" * 78)
    print("Knowledge base retrieval evaluation")
    print("=" * 78)
    print(f"corpus: {len(docs)} docs ({sum(d.stat().st_size for d in docs) / 1024:.0f} KB), "
          f"cases: {len(CASES)}, top-k: {args.top_k}")

    SCRATCH_DB.parent.mkdir(parents=True, exist_ok=True)
    alphas = [args.alpha] if args.alpha is not None else [0.0, 0.2, 0.3, 0.5]

    results: list[tuple[str, int, dict]] = []
    import knowledge

    pools = [args.pool] if args.pool is not None else [knowledge.RERANK_POOL]
    for size, overlap in configs:
        chunks = build_index(docs, size, overlap)
        for alpha in alphas:
            for pool in pools:
                m = evaluate(CASES, args.top_k, alpha, pool)
                results.append(
                    (
                        f"size={size} overlap={overlap}",
                        chunks,
                        {"alpha": alpha, "pool": pool, **m},
                    )
                )

    print()
    print("=" * 78)
    print(
        f"{'config':<24}{'chunks':>7}{'alpha':>7}{'pool':>6}"
        f"{'hit@1':>8}{f'hit@{args.top_k}':>8}{'MRR':>8}"
    )
    print("-" * 78)
    for cfg, chunks, m in results:
        print(
            f"{cfg:<24}{chunks:>7}{m['alpha']:>7.1f}{m['pool']:>6}"
            f"{m['hit@1'] * 100:>7.0f}%{m['hit@k'] * 100:>7.0f}%{m['mrr']:>8.2f}"
        )
    print("=" * 78)

    best = max(results, key=lambda r: (r[2]["hit@1"], r[2]["hit@k"], r[2]["mrr"]))
    print(f"best: {best[0]} alpha={best[2]['alpha']}  "
          f"hit@1={best[2]['hit@1'] * 100:.0f}%  MRR={best[2]['mrr']:.2f}")
    if best[2]["misses"]:
        print("\nmisses at the best config:")
        for miss in best[2]["misses"]:
            print(f"  - {miss}")

    SCRATCH_DB.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
