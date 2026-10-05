#!/usr/bin/env python3
"""Cross-layer connectivity check: data -> data access -> logic -> interface.

What this answers
-----------------
"Are the layers really connected through interfaces, or is the data simply hard-coded in the
code and the tests only prove internal consistency of constants?"

The decisive test is **(1a): empty the data layer**. Hard-coded answers would still be produced
with an empty database; a real layered design must report "found nothing". Everything after that
only confirms the positive direction:

    1a  empty data layer              -> tools report nothing (answers are not code constants)
    1b  row written by an EXTERNAL sqlite3 client -> immediately visible to the tools
    1c  data FILE -> loader -> data layer -> tools -> the values from the file show up
    2   the code's declared columns vs the DB's real schema (a drift here breaks at runtime)
    3   stub the data-access function -> the tool result changes (the call goes through the
        layer interface, not through inlined logic)
    4   the logic layer is reachable through the interface layer (FunctionSchema handlers)
    5   transport layer (model -> tool call) is covered by verify_tools.py

Everything runs against a scratch database and scratch data files, so the real data is untouched.
Exit code 0 = every boundary check passed.

Usage:
    cd server && uv run ../verify_layers.py
"""

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "server"))

# Point every layer at scratch data BEFORE importing the modules (they read env at import time).
SCRATCH = Path(tempfile.mkdtemp(prefix="verify-layers-"))
SCRATCH_DB = SCRATCH / "memory.db"
SCRATCH_BUSINESS = SCRATCH / "demo-business.json"
SCRATCH_TOOLS = SCRATCH / "sample-tools.json"

import os  # noqa: E402

os.environ["MEMORY_DB"] = str(SCRATCH_DB)
os.environ["DEMO_DATA_FILE"] = str(SCRATCH_BUSINESS)
os.environ["SAMPLE_TOOLS_DATA"] = str(SCRATCH_TOOLS)

# Start from the committed data files so the run is quiet; section [4] then swaps them, which is
# what demonstrates that changing data needs no code change.
SCRATCH_TOOLS.write_text(
    (BASE / "sample-data" / "sample-tools.json").read_text(encoding="utf-8"), encoding="utf-8"
)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE / "server" / ".env")
# load_dotenv does not override existing env vars, so the scratch paths above survive.

import asyncio  # noqa: E402

import memory  # noqa: E402
import sample_tools  # noqa: E402
import tools  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((ok, label, detail))
    print(f"  [{'OK' if ok else 'ERR'}]   {label}" + (f" -- {detail}" if detail else ""))
    return ok


class Params:
    """Minimal FunctionCallParams stand-in (what the framework passes to a handler)."""

    def __init__(self, arguments: dict):
        self.arguments = arguments
        self.result: dict | None = None

    async def result_callback(self, result, properties=None):  # noqa: ANN001
        self.result = result


def call(handler, arguments: dict) -> dict:
    params = Params(arguments)
    asyncio.run(handler(params))
    return params.result or {}


def external_write(order_id: str, customer: str) -> None:
    """Write a row with a raw sqlite3 connection: not through any function of this project."""
    conn = sqlite3.connect(memory.DB_PATH)
    conn.execute(
        "INSERT INTO orders (order_id, customer, status, amount, updated_at) VALUES (?,?,?,?,?)",
        (order_id, customer, "待发货", 12.5, time.time()),
    )
    conn.commit()
    conn.close()


def main() -> int:
    print("=" * 78)
    print("cross-layer connectivity check (data / data-access / logic / interface)")
    print("=" * 78)
    print(f"scratch data layer: {SCRATCH}")

    memory.init_db()

    print("\n[1] data layer -> data-access layer")
    # 1a: the falsification. With nothing in the data layer there is nothing to answer from.
    empty = call(tools.query_data, {"table": "orders", "limit": 5})
    check(
        "empty data layer -> tool reports nothing (answer is not a code constant)",
        empty.get("found") is False and not empty.get("rows"),
        f"spoken={empty.get('spoken')!r}",
    )

    # 1a-bis: "no data" must never become a fake success or a nonsense value read aloud.
    agg = call(tools.query_data, {"table": "orders", "aggregate": "sum:amount"})
    check(
        "empty data layer -> aggregate says 'nothing found', never reads out 'None'",
        agg.get("found") is False and "None" not in str(agg.get("spoken")),
        f"spoken={agg.get('spoken')!r}",
    )

    # 1b: an external writer is visible immediately -> the tool really reads the data layer.
    external_write("EXT-1", "外部写入")
    hit = call(tools.query_data, {"table": "orders", "filters": {"order_id": "EXT-1"}})
    check(
        "row written by an EXTERNAL sqlite3 client is visible to the tool",
        hit.get("count") == 1 and hit["rows"][0]["customer"] == "外部写入",
    )

    # 1c: the data FILE for demo rows -> loader -> data layer -> tool.
    business = json.loads((BASE / "sample-data" / "demo-business.json").read_text(encoding="utf-8"))
    business["orders"] = [
        {"order_id": "FILE-1", "customer": "来自数据文件", "status": "已完成", "amount": 1.0}
    ]
    SCRATCH_BUSINESS.write_text(json.dumps(business, ensure_ascii=False), encoding="utf-8")
    loaded = memory.seed_demo_business(SCRATCH_BUSINESS)
    from_file = call(tools.query_data, {"table": "orders", "filters": {"order_id": "FILE-1"}})
    check(
        "data FILE -> loader -> data layer -> tool sees the file's values",
        loaded >= 1 and from_file.get("count") == 1,
        f"loaded {loaded} rows from {SCRATCH_BUSINESS.name}",
    )

    print("\n[2] code layer's declared interface vs the data layer's real schema")
    conn = sqlite3.connect(memory.DB_PATH)
    real_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing: dict[str, list[str]] = {}
    for table, columns in memory.TABLE_COLUMNS.items():
        if table not in real_tables:
            missing[table] = ["<table missing>"]
            continue
        real_columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        absent = sorted(set(columns) - real_columns)
        if absent:
            missing[table] = absent
    conn.close()
    check(
        "every column the code declares exists in the database",
        not missing,
        f"{len(memory.TABLE_COLUMNS)} tables checked" if not missing else f"drift: {missing}",
    )

    print("\n[3] data-access layer -> logic layer")
    original = memory.query_table
    try:
        memory.query_table = lambda **_kwargs: {  # type: ignore[assignment]
            "table": "orders",
            "count": 1,
            "rows": [{"customer": "来自被替换的数据访问层"}],
        }
        stubbed = call(tools.query_data, {"table": "orders"})
        check(
            "stubbing the data-access function changes the tool result",
            stubbed.get("rows") == [{"customer": "来自被替换的数据访问层"}],
            "the tool calls through the layer interface, it does not inline the query",
        )
    finally:
        memory.query_table = original  # type: ignore[assignment]

    print("\n[4] logic layer -> interface layer")
    schemas = tools.build_tools("probe-session").standard_tools
    names = [s.name for s in schemas]
    check(
        "every tool is exposed as an interface with a handler",
        len(schemas) >= 12 and all(s.handler is not None for s in schemas),
        f"{len(schemas)} tools: {', '.join(names)}",
    )
    schema = next(s for s in schemas if s.name == "query_data")
    before = call(schema.handler, {"table": "orders", "aggregate": "count"})["result"]
    external_write("EXT-2", "第二次外部写入")
    after = call(schema.handler, {"table": "orders", "aggregate": "count"})["result"]
    check(
        "the same interface, called again after an external write, answers differently",
        after == before + 1,
        f"{before} -> {after}",
    )
    weather_schema = next(s for s in schemas if s.name == "get_weather")
    SCRATCH_TOOLS.write_text(
        json.dumps(
            {"weather": {"火星": {"temperature_c": -60, "condition": "沙尘"}}, "devices": ["月球车"]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sample_tools.load_demo_data(force=True)
    alt = call(weather_schema.handler, {"city": "火星"})
    check(
        "swapping the data FILE changes what the interface answers (no code change)",
        alt.get("temperature_c") == -60,
        f"火星 -> {alt.get('condition')} {alt.get('temperature_c')}C",
    )

    print("\n[5] transport layer (model -> tool call)")
    print("  [INFO] the real LLM call chain is covered by verify_tools.py (one live call)")

    failed = [label for ok, label, _ in RESULTS if not ok]
    print("\n" + "-" * 78)
    print(f"checks: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    ok = not failed
    print(
        "verdict:",
        "[OK] every layer boundary is connected for real"
        if ok
        else f"[ERR] failed: {'; '.join(failed)}",
    )
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
