"""Ingest documents into the local knowledge base (the "write side" of RAG).

Why writes and queries are separated (consistent with HANDBOOK-02):
    Retrieval inside a tool must return **synchronously in milliseconds**. If retrieval
    parsed files and computed embeddings on the fly, the user would wait seconds -- fatal for
    voice. So parsing / chunking / embedding all happen offline here and are written to the
    DB; at runtime the tool only queries the DB.

Usage:
    cd server
    uv run ../scripts/ingest_docs.py ../README.md                     # single file
    uv run ../scripts/ingest_docs.py --dir ./docs                     # directory (recursive, .md/.txt)
    uv run ../scripts/ingest_docs.py ../README.md --source manual     # custom source name
    uv run ../scripts/ingest_docs.py --url <URL>                      # fetch a real page/API and ingest it
    uv run ../scripts/ingest_docs.py --list                           # list ingested docs (no model load)
    uv run ../scripts/ingest_docs.py --dir ./docs --prune             # also drop docs whose file is gone
    uv run ../scripts/ingest_docs.py --delete <source>                # delete one document by source

``--url`` is how the write side reaches a **real** external source instead of a local file: it
does one HTTP GET, keeps the readable text and stores it under the URL as its source. Handle
both shapes -- an HTML page (tags stripped) and a JSON API whose payload carries an ``extract``
field. It is a one-off fetch, not a crawler: respect the target site's terms and robots.txt.
"""

import argparse
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

# Scripts live in scripts/; the project root (server/, sample-data/, docs/) is one level up.
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "server"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE / "server" / ".env", override=True)

import knowledge  # noqa: E402

SUFFIXES = {".md", ".txt", ".markdown"}


# ---------------------------------------------------------------- fetch from the web
#
# A descriptive User-Agent is what most sites (Wikipedia included) ask for; timeout so a hung
# server cannot wedge the script.
USER_AGENT = "pipecat-quickstart-kb/1.0 (one-off document ingest; contact: repo owner)"
FETCH_TIMEOUT = 30.0


class _TextExtractor(HTMLParser):
    """Collect visible text, skipping script / style / noscript content."""

    def __init__(self) -> None:
        super().__init__()
        self._skipping = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in ("script", "style", "noscript"):
            self._skipping += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self._skipping and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    """Visible text of an HTML document (stdlib only -- no new dependency for this)."""
    parser = _TextExtractor()
    parser.feed(html)
    return "\n".join(parser.parts)


def fetch_url(url: str) -> tuple[str, str]:
    """Fetch one URL and return ``(text, content_type)``.

    JSON payloads that carry an ``extract`` (Wikipedia REST and similar) are used directly;
    HTML is reduced to its visible text. Only http(s) is accepted.
    """
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"only http(s) URLs are supported, got {scheme or 'no scheme'!r}")

    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=FETCH_TIMEOUT) as response:  # noqa: S310 - operator-supplied URL
        raw = response.read()
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"

    body = raw.decode(charset, errors="replace")
    if "json" in content_type:
        data = json.loads(body)
        if isinstance(data, dict) and data.get("extract"):
            return f"{data.get('title', '')}\n{data['extract']}".strip(), content_type
        raise ValueError(f"{url}: JSON payload has no 'extract' field")
    if "html" in content_type or body.lstrip().startswith("<"):
        return html_to_text(body), content_type
    return body, content_type


def title_for(url: str, text: str) -> str:
    """Reuse the page's own first line as the title when it looks like one."""
    first = (text.strip().splitlines() or [""])[0].strip()
    if 0 < len(first) <= 60:
        return first
    return unquote(urlparse(url).path.rsplit("/", 1)[-1]) or url


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


def should_list(args) -> bool:  # noqa: ANN001
    """Whether this invocation is "list mode".

    A bare ``--url`` is an **ingest**, not a list request -- getting this wrong made
    ``ingest_docs.py --url ...`` print the document list and silently skip the fetch.
    """
    return bool(args.list) or (not args.paths and not args.dir and not args.url)


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
    ap.add_argument(
        "--url",
        action="append",
        default=None,
        metavar="URL",
        help="fetch a real page/API over HTTP and ingest it (repeatable)",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="run SQLite's integrity check on the knowledge base and exit",
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

    if args.check:
        report = knowledge.integrity_check()
        print(f"knowledge db integrity: {report}")
        if report != "ok":
            print("  -> the file is damaged. Every source can be re-fetched, so rebuild it:")
            print("     rm server/data/knowledge.db")
            print("     uv run ../scripts/ingest_docs.py <your documents / --url ...>")
            return 1
        return 0

    if should_list(args):
        st = knowledge.stats()
        print(f"knowledge base: {st['documents']} documents / {st['chunks']} chunks")
        for d in knowledge.list_documents():
            print(f"  - {d['source']} ({d['chunks']} chunks) {d['title']}")
        return 0

    urls = args.url or []
    files = collect(args.paths, args.dir)
    if not files and not urls:
        print("no ingestable files found (supports .md/.txt, or pass --url)")
        return 1

    # Silently ignoring a user-specified flag is exactly the kind of thing this project
    # tries to avoid, so say it out loud instead.
    if args.source and len(files) != 1:
        print(f"  [warn] --source is ignored with {len(files)} files (it applies to a single file)")

    print("=" * 70)
    print(
        f"ingesting {len(files)} file(s) and {len(urls)} URL(s) "
        "(the embedding model loads on first use, please wait)"
    )
    print("=" * 70)
    total = 0
    failed = 0

    for url in urls:
        try:
            text, content_type = fetch_url(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] fetch failed {url}: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        try:
            n = knowledge.ingest(text, source=url, title=title_for(url, text))
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] ingest failed {url}: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        total += n
        print(f"  [OK] {url} -> {n} chunks ({content_type}, {len(text)} chars fetched)")

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
