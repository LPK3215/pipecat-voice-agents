"""Business data ingestion example: write an external source into the local DB (the tool's
query side never touches external systems).

This is the template for "**ingestion and querying must be separated**" (HANDBOOK-02 section 3):

    external source --[this script, can run on a schedule]--> local DB --[query_data tool, on demand]--> model

Why separation is mandatory:
    Tools **wait synchronously for a result**. If a tool scraped a page or called an API on
    the fly, the user would wait seconds (fatal for voice); and if the external source went
    down, the tool would fail too. Once the data is in the DB, the tool only queries locally --
    fast and unaffected by external outages.

To change the data source, **only edit ``fetch_from_source``**; the tool, schema, and prompts
stay untouched.

NOTE: DEMO_ORDERS data and the default status below are intentionally Chinese -- demo
business data for a Chinese-facing assistant.

Usage:
    cd server
    uv run ../scripts/collect_orders.py                    # built-in demo data
    uv run ../scripts/collect_orders.py --csv orders.csv   # import from CSV (order_id,customer,status,amount)
    uv run ../scripts/collect_orders.py --url <URL>        # fetch a real public JSON API and map its records
    uv run ../scripts/collect_orders.py --list             # show current orders

``--url`` is the "real network source" example: one HTTP GET against a public API (no key), each
record mapped explicitly onto the orders table by ``map_api_item``. The mapping is a *business*
decision, so it is written out in the open -- change those few lines for your own source.
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

# Scripts live in scripts/; the project root (server/, sample-data/, docs/) is one level up.
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "server"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE / "server" / ".env", override=True)

import memory  # noqa: E402

# Built-in demo: when wiring a real source, replace fetch_from_source with "scrape the
# monitoring page / call an internal API / read a file".
DEMO_ORDERS = [
    {"order_id": "A20261001004", "customer": "赵六", "status": "待发货", "amount": 56.0},
    {"order_id": "A20261001005", "customer": "钱七", "status": "已发货", "amount": 420.0},
]

# Polite defaults for the real-network example (a descriptive UA is what public APIs ask for).
USER_AGENT = "pipecat-quickstart-collector/1.0 (one-off demo ingest; contact: repo owner)"
FETCH_TIMEOUT = 30.0


def map_api_item(item: dict) -> dict:
    """Map **one record** of the external API onto the orders table -- explicitly.

    Change these four lines for your own source; nothing else in the pipeline moves. Demo
    target: GitHub's public releases API (real, published records, no key required).
    """
    return {
        "order_id": str(item.get("tag_name") or item.get("id") or "").strip(),
        "customer": str((item.get("author") or {}).get("login") or "").strip(),
        "status": "预发布" if item.get("prerelease") else "已发布",
        "amount": 0.0,  # this source has no amount field; map yours here
    }


def fetch_json(url: str) -> list[dict]:
    """HTTP GET a public JSON API and map its records to the orders shape."""
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=FETCH_TIMEOUT) as response:  # noqa: S310 - operator-supplied URL
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("items") or [payload]
    if not isinstance(payload, list):
        raise ValueError(f"{url}: expected a JSON array or object, got {type(payload).__name__}")
    return [map_api_item(item) for item in payload if isinstance(item, dict)]


def fetch_from_source(csv_path: Path | None = None, url: str | None = None) -> list[dict]:
    """Fetch rows from the external source.

    **This is the only place to rewrite for a real source.** Three working shapes ship here:
    built-in demo rows, a local CSV export, and a real public JSON API (``--url``).
    """
    if url:
        return fetch_json(url)
    if csv_path is None:
        return DEMO_ORDERS
    rows: list[dict] = []
    with csv_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(
                {
                    "order_id": (row.get("order_id") or "").strip(),
                    "customer": (row.get("customer") or "").strip(),
                    "status": (row.get("status") or "待发货").strip(),
                    "amount": float(row.get("amount") or 0),
                }
            )
    return [r for r in rows if r["order_id"]]


def main() -> int:
    ap = argparse.ArgumentParser(description="ingest order data into the local DB")
    ap.add_argument("--csv", type=Path, default=None, help="CSV file (built-in demo data if omitted)")
    ap.add_argument("--url", default=None, metavar="URL", help="public JSON API to fetch records from")
    ap.add_argument("--list", action="store_true", help="list current orders and exit")
    args = ap.parse_args()

    memory.init_db()

    if args.list:
        rows = memory.query_table("orders", limit=50)["rows"]
        print(f"current orders: {len(rows)}")
        for r in rows:
            print(f"  {r.get('order_id')} | {r.get('customer')} | {r.get('status')} | {r.get('amount')}")
        return 0

    items = fetch_from_source(args.csv, url=args.url)
    for it in items:
        memory.upsert_order(
            order_id=it["order_id"],
            customer=it["customer"],
            status=it["status"],
            amount=it["amount"],
        )
    total = memory.query_table("orders", aggregate="count")["result"]
    print(f"ingestion done: {len(items)} this run; {total} now in the local DB")
    print("hint: the query_data tool can now query the orders table (table=orders).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
