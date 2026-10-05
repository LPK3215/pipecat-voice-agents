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
    uv run ../ingest_docs.py --dir ./docs --prune             # also drop docs whose file is gone
    uv run ../ingest_docs.py --delete <source>                # delete one document by source
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


def doc_source(path: Path) -> str:
    """Stable document identity: the path relative to the project root when possible.

    Why not the path **as typed** (the old behaviour): ``../README.md`` (run from ``server/``)
    and ``README.md`` (run from the project root) are the same file, but were stored as two
    documents -- retrieval then returned duplicate chunks from both copies and ``--delete``
    needed the exact original spelling. Resolving first makes a re-ingest from any working
    directory update the same document.
    """
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(BASE))
    except ValueError:  # a file outside the project (e.g. /tmp/notes.md)
        return str(resolved)


def prune_missing() -> list[str]:
    """Delete documents whose source file no longer exists (stale entries after a re-ingest).

    Only sources that look like ingested files are considered: a custom ``--source name``
    has no suffix (or no path) and is therefore never pruned by accident.
    """
    removed: list[str] = []
    for doc in knowledge.list_documents():
        source = doc["source"]
        if Path(source).suffix.lower() not in SUFFIXES:
            continue  # a custom source name, not a file path -- not ours to guess about
        path = Path(source)
        if not path.is_absolute():
            path = BASE / path
        if not path.exists() and knowledge.delete_document(source):
            removed.append(source)
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description="ingest documents into the local knowledge base")
    ap.add_argument("paths", nargs="*", type=Path, help="files or directories to ingest")
    ap.add_argument("--dir", type=Path, default=None, help="directory (recursively collects .md/.txt)")
    ap.add_argument("--source", default=None, help="custom source name (single file only)")
    ap.add_argument("--list", action="store_true", help="list ingested documents and exit")
    ap.add_argument("--delete", default=None, metavar="SOURCE", help="delete a document by source and exit")
    ap.add_argument(
        "--prune",
        action="store_true",
        help="delete documents whose source file no longer exists (then continue, if paths given)",
    )
    args = ap.parse_args()

    knowledge.init_db()

    if args.delete:
        removed = knowledge.delete_document(args.delete)
        print(f"{'removed' if removed else 'not found'}: {args.delete}")
        return 0 if removed else 1

    if args.prune:
        pruned = prune_missing()
        print(f"pruned {len(pruned)} stale document(s)" + (f": {', '.join(pruned)}" if pruned else ""))
        if not args.paths and not args.dir:
            return 0

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

    # Silently ignoring a user-specified flag is exactly the kind of thing this project
    # tries to avoid, so say it out loud instead.
    if args.source and len(files) != 1:
        print(f"  [warn] --source is ignored with {len(files)} files (it applies to a single file)")

    print("=" * 70)
    print(f"ingesting {len(files)} file(s) (the embedding model loads on first use, please wait)")
    print("=" * 70)
    total = 0
    failed = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] read failed {f}: {exc}")
            failed += 1
            continue
        source = args.source if (args.source and len(files) == 1) else doc_source(f)
        try:
            n = knowledge.ingest(text, source=source, title=f.stem)
            total += n
            print(f"  [OK] {source} -> {n} chunks")
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] ingest failed {f}: {type(exc).__name__}: {exc}")
            failed += 1

    st = knowledge.stats()
    print("-" * 70)
    print(
        f"done: {total} chunks this run; total {st['documents']} documents / {st['chunks']} chunks"
        + (f"; {failed} file(s) FAILED" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
