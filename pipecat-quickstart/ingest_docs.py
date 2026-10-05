"""Ingest documents into the local knowledge base (the "write side" of RAG).

Why writes and queries are separated (consistent with HANDBOOK-02):
    Retrieval inside a tool must return **synchronously in milliseconds**. If retrieval
    parsed files and computed embeddings on the fly, the user would wait seconds -- fatal for
    voice. So parsing / chunking / embedding all happen offline here and are written to the
    DB; at runtime the tool only queries the DB.

Usage:
    cd server
    uv run ../ingest_docs.py ../README.md                     # single file
    uv run ../ingest_docs.py --dir ./docs                     # directory (recursive, .md/.txt)
    uv run ../ingest_docs.py ../README.md --source manual     # custom source name
    uv run ../ingest_docs.py --list                           # list ingested docs (no model load)
"""

import argparse
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "server"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE / "server" / ".env", override=True)

import knowledge  # noqa: E402

SUFFIXES = {".md", ".txt", ".markdown"}


def collect(paths: list[Path], directory: Path | None) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files += [f for f in sorted(p.rglob("*")) if f.suffix.lower() in SUFFIXES]
        elif p.is_file():
            files.append(p)
        else:
            print(f"  [skip] does not exist: {p}")
    if directory:
        files += [f for f in sorted(directory.rglob("*")) if f.suffix.lower() in SUFFIXES]
    # Deduplicate while preserving order.
    seen, out = set(), []
    for f in files:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            out.append(f)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="ingest documents into the local knowledge base")
    ap.add_argument("paths", nargs="*", type=Path, help="files or directories to ingest")
    ap.add_argument("--dir", type=Path, default=None, help="directory (recursively collects .md/.txt)")
    ap.add_argument("--source", default=None, help="custom source name (single file only)")
    ap.add_argument("--list", action="store_true", help="list ingested documents and exit")
    args = ap.parse_args()

    knowledge.init_db()

    if args.list or (not args.paths and not args.dir):
        st = knowledge.stats()
        print(f"knowledge base: {st['documents']} documents / {st['chunks']} chunks")
        for d in knowledge.list_documents():
            print(f"  - {d['source']} ({d['chunks']} chunks) {d['title']}")
        return 0

    files = collect(args.paths, args.dir)
    if not files:
        print("no ingestable files found (supports .md/.txt)")
        return 1

    print("=" * 70)
    print(f"ingesting {len(files)} file(s) (the embedding model loads on first use, please wait)")
    print("=" * 70)
    total = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] read failed {f}: {exc}")
            continue
        source = args.source if (args.source and len(files) == 1) else str(f)
        try:
            n = knowledge.ingest(text, source=source, title=f.stem)
            total += n
            print(f"  [OK] {source} -> {n} chunks")
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] ingest failed {f}: {type(exc).__name__}: {exc}")

    st = knowledge.stats()
    print("-" * 70)
    print(f"done: {total} chunks this run; total {st['documents']} documents / {st['chunks']} chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
