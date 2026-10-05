"""Local persistence: session history + long-term memory + business data tables.

Why write this layer ourselves:
    pipecat provides **no persistence at all**. It ships only "short-term memory" -- the
    message list inside ``LLMContext``, which is gone when the process exits; long-term
    memory is only a mem0 adapter (``services/mem0/memory.py``), and mem0's cloud needs a
    key and may charge. There is nothing for a knowledge base (vector retrieval).

    Conclusion: all three must be built here. SQLite is a zero-dependency, zero-cost,
    zero-ops starting point; to move to mem0 or a vector store later, **the replacement
    point is inside this module** -- the external surface stays "read a block of text into
    the context" or "register a tool", so upper layers do not change.

The three tables and their roles:
    turns    session history (persisted short-term memory): keep chatting after a restart
    facts    long-term memory: user preferences, agreements, conclusions across sessions
    metrics  business data: metrics pulled from external systems, queried by tools

On "an AI system = short-term memory + long-term memory + skills + knowledge base + DB":
    This module covers "long-term memory + database"; "short-term memory" is the
    framework's LLMContext (this module only persists it); "skills" is tools.py.

NOTE: demo business rows and a few context-injection strings below are intentionally
Chinese -- they are data and prompts for a Chinese-speaking agent.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.observers.base_observer import BaseObserver

# Database file path. Defaults to server/data/, separate from the code.
DB_PATH = Path(
    os.getenv("MEMORY_DB", str(Path(__file__).resolve().parent / "data" / "memory.db"))
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, id);

CREATE TABLE IF NOT EXISTS facts (
    key        TEXT    PRIMARY KEY,
    value      TEXT    NOT NULL,
    source     TEXT    NOT NULL DEFAULT 'user',
    created_at REAL    NOT NULL,
    updated_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    value      REAL    NOT NULL,
    unit       TEXT    NOT NULL DEFAULT '',
    note       TEXT    NOT NULL DEFAULT '',
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name, id);

CREATE TABLE IF NOT EXISTS hosts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    ip         TEXT    NOT NULL DEFAULT '',
    region     TEXT    NOT NULL DEFAULT '',
    latency_ms REAL    NOT NULL DEFAULT 0,
    cpu_pct    REAL    NOT NULL DEFAULT 0,
    status     TEXT    NOT NULL DEFAULT 'running',
    created_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    host       TEXT    NOT NULL,
    level      TEXT    NOT NULL DEFAULT 'warning',
    message    TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'firing',
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status, id);

CREATE TABLE IF NOT EXISTS orders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   TEXT    NOT NULL UNIQUE,
    customer   TEXT    NOT NULL DEFAULT '',
    status     TEXT    NOT NULL DEFAULT '待发货',
    amount     REAL    NOT NULL DEFAULT 0,
    updated_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, id);
"""


def _conn() -> sqlite3.Connection:
    """Get a connection.

    SQLite calls are millisecond scale, so the synchronous API is used directly; for much
    higher volume switch to aiosqlite -- the replacement point is inside this function.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables. Repeated calls are side-effect free (all IF NOT EXISTS)."""
    with _conn() as conn:
        conn.executescript(_SCHEMA)


def new_session_id() -> str:
    """One session ID per connection. Short, to be readable in logs."""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------- session history


def save_turn(session_id: str, role: str, content: str) -> None:
    """Save one turn. Empty content is skipped -- interruptions and silence yield empty text."""
    text = (content or "").strip()
    if not text:
        return
    with _conn() as conn:
        conn.execute(
            "INSERT INTO turns (session_id, role, content, created_at) VALUES (?,?,?,?)",
            (session_id, role, text, time.time()),
        )


def recent_turns(session_id: str | None = None, limit: int = 20) -> list[dict]:
    """Fetch the most recent turns.

    When ``session_id`` is empty it queries across sessions (for "where did we leave off"
    recovery scenarios).
    """
    with _conn() as conn:
        if session_id:
            rows = conn.execute(
                "SELECT role, content FROM turns WHERE session_id=? "
                "ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT role, content FROM turns ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ---------------------------------------------------------------- long-term memory


def put_fact(key: str, value: str, source: str = "user") -> None:
    """Store one long-term fact. Same key overwrites (created_at is preserved)."""
    now = time.time()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO facts (key, value, source, created_at, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value, source=excluded.source,
                   updated_at=excluded.updated_at""",
            (key, value, source, now, now),
        )


def get_fact(key: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT key, value, updated_at FROM facts WHERE key=?", (key,)
        ).fetchone()
    return dict(row) if row else None


def delete_fact(key: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM facts WHERE key=?", (key,))
    return cur.rowcount > 0


def list_facts(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT key, value FROM facts ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def search_facts(query: str, limit: int = 5) -> list[dict]:
    """Keyword search over long-term memory.

    Before a vector store exists, this is the minimum viable "knowledge base": substring
    matching with a tokenization fallback. Chinese has no spaces, so the query is split
    into 2-character sliding windows and OR-matched, which hits far more often than a
    whole-string LIKE. To switch to vector retrieval, this function is the replacement point.
    """
    q = (query or "").strip()
    if not q:
        return []

    terms = [q]
    # Chinese: 2-character sliding windows; English/digits: split on spaces.
    if re.search(r"[\u4e00-\u9fff]", q):
        cleaned = re.sub(r"[^\u4e00-\u9fff0-9a-zA-Z]", "", q)
        terms += [cleaned[i : i + 2] for i in range(len(cleaned) - 1)]
    else:
        terms += q.split()
    terms = [t for t in dict.fromkeys(terms) if len(t) >= 2][:8]
    if not terms:
        # A single-character query (e.g. one Han character) becomes an empty set after the
        # split/filter above; continuing would produce "WHERE  ORDER BY ...", a syntax error.
        return []

    where = " OR ".join(["key LIKE ? OR value LIKE ?"] * len(terms))
    params: list = []
    for t in terms:
        params += [f"%{t}%", f"%{t}%"]

    with _conn() as conn:
        rows = conn.execute(
            f"SELECT key, value FROM facts WHERE {where} "
            f"ORDER BY updated_at DESC LIMIT ?",  # noqa: S608 - terms come from local splitting
            (*params, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- business data


def record_metric(
    name: str, value: float, unit: str = "", note: str = ""
) -> None:
    """Insert one business metric. Called by the external ingestion script."""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO metrics (name, value, unit, note, created_at) VALUES (?,?,?,?,?)",
            (name, value, unit, note, time.time()),
        )


def upsert_order(
    order_id: str, customer: str = "", status: str = "待发货", amount: float = 0.0
) -> None:
    """Insert/update one order (called by the **ingestion script**).

    Upsert rather than plain insert: the order id is unique, so re-running the ingestion
    script must not create duplicate rows. (``record_metric`` is a plain insert because it
    appends by time and cannot duplicate.)
    """
    with _conn() as conn:
        conn.execute(
            """INSERT INTO orders (order_id, customer, status, amount, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(order_id) DO UPDATE SET
                   customer=excluded.customer, status=excluded.status,
                   amount=excluded.amount, updated_at=excluded.updated_at""",
            (order_id, customer, status, float(amount), time.time()),
        )


def query_metrics(name: str | None = None, limit: int = 5) -> list[dict]:
    """Query the latest metrics. With empty ``name``, the latest row per metric name."""
    with _conn() as conn:
        if name:
            rows = conn.execute(
                "SELECT name, value, unit, note, created_at FROM metrics "
                "WHERE name LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{name}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT name, value, unit, note, created_at FROM metrics m
                   WHERE id = (SELECT MAX(id) FROM metrics WHERE name = m.name)
                   ORDER BY name LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


class TurnRecorder(BaseObserver):
    """Write each conversation turn into the ``turns`` table.

    Why it is needed: the framework's ``LLMContext`` is in memory only, so a restart loses
    everything.

    Why an **observer (BaseObserver) and not a pipeline processor**:
        User-side text (``TranscriptionFrame``) is consumed by ``LLMUserAggregator`` and
        assistant-side text (``LLMTextFrame``) by ``LLMAssistantAggregator`` -- in the
        pipecat source both branches **do not push downstream** (the user aggregator's
        comment literally says "consumed here and not pushed downstream"). So no single
        processor position can see both sides. An earlier version attached it at the end of
        the pipeline and the ``turns`` table was **never written**. An observer sees every
        frame's **first push** regardless of position, which is the reliable way to persist.

    On the assistant side, persist at ``LLMFullResponseEndFrame``: a reply is streamed, and
    saving per frame would split one sentence into dozens of rows. The buffer is cleared at
    ``LLMFullResponseStartFrame`` so a truncated reply from a previous turn cannot leak in.

    The user side has **two input paths**, both must be recorded:
        voice input -> ``TranscriptionFrame`` (produced by STT)
        text input  -> ``LLMMessagesAppendFrame`` (produced by RTVI ``send-text``,
                       **not** a TranscriptionFrame)
    Recording only the former leaves keyboard-asked sessions missing their user half.
    """

    def __init__(self, session_id: str, **kwargs) -> None:
        # Only observe the first push: every push notifies, so without dedup rows duplicate.
        kwargs.setdefault("observe_every_push", False)
        super().__init__(**kwargs)
        self._session_id = session_id
        self._pending: list[str] = []

    async def on_push_frame(self, data) -> None:
        frame = data.frame

        if isinstance(frame, TranscriptionFrame):
            save_turn(self._session_id, "user", frame.text)
        elif isinstance(frame, LLMMessagesAppendFrame):
            # The text channel (RTVI send-text) arrives here; only take the user role.
            for msg in getattr(frame, "messages", None) or []:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    save_turn(self._session_id, "user", str(msg.get("content", "")))
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._pending = []
        elif isinstance(frame, LLMTextFrame):
            self._pending.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._pending:
                save_turn(self._session_id, "assistant", "".join(self._pending))
                self._pending.clear()


def load_memory_into_context(context, limit: int = 10) -> int:
    """Inject long-term memory and the previous session into the LLMContext.

    This is where "long-term memory" actually takes effect: the framework does not care how
    you store it, but you must inject it **when building the context** for the model to see
    it. Returns the number of injected messages.

    NOTE: the injected text is intentionally Chinese -- it is a prompt for a Chinese agent.
    """
    injected = 0
    facts = list_facts(limit=20)
    if facts:
        lines = "\n".join(f"- {f['key']}: {f['value']}" for f in facts)
        context.add_message(
            {
                "role": "user",
                "content": f"以下是你需要记住的关于用户的信息：\n{lines}",
            }
        )
        injected += 1
    return injected


# ------------------------------------------------- structured knowledge base (generic query)
#
# This is where "the database is also a knowledge base" lands:
#   vector KB     : unstructured documents -> semantic similarity -> a passage matches
#   the DB here   : structured data         -> exact conditions   -> rows match
# Both coexist and both are exposed as **one tool** (see tools.py::query_data).
#
# Safety design: **the model never writes SQL**. It only submits structured parameters
# (table / filters / order_by / aggregate), which are whitelist-validated here before a
# parameterized statement is built. Table and column names must be within the whitelist,
# so nothing outside it is reachable.

# Whitelist: table name -> queryable columns
TABLE_COLUMNS: dict[str, list[str]] = {
    "metrics": ["name", "value", "unit", "note", "created_at"],
    "hosts": ["name", "ip", "region", "latency_ms", "cpu_pct", "status", "created_at"],
    "alerts": ["id", "host", "level", "message", "status", "created_at"],
    "orders": ["id", "order_id", "customer", "status", "amount", "updated_at"],
}

# Numeric columns that support aggregation
NUMERIC_COLUMNS: dict[str, list[str]] = {
    "metrics": ["value"],
    "hosts": ["latency_ms", "cpu_pct"],
    "alerts": ["id"],
    "orders": ["amount"],
}

_AGG_FUNCS = ("count", "avg", "max", "min", "sum")


def _fmt_row(table: str, row: sqlite3.Row) -> dict:
    """Convert timestamps to readable time. The model reads '2026-10-01 23:55' better than 1790870057."""
    out = dict(row)
    ts = out.pop("created_at", None)
    if ts is not None:
        out["time"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    return out


def query_table(
    table: str,
    filters: dict | None = None,
    search: str = "",
    order_by: str = "",
    desc: bool = True,
    limit: int = 10,
    aggregate: str = "",
) -> dict:
    """Generic structured query. The model submits parameters via a tool; this validates and runs.

    ``aggregate`` accepts ``count`` / ``avg:column`` / ``max:column`` etc.; when an
    aggregate is given, order_by and limit are ignored and a single number is returned --
    so the model can answer "how many unresolved alerts" without pulling all rows to count.
    """
    if table not in TABLE_COLUMNS:
        return {
            "error": f"未知的表 {table!r}",
            "available_tables": list(TABLE_COLUMNS),
        }

    cols = TABLE_COLUMNS[table]
    where_sql: list[str] = []
    params: list = []

    for col, val in (filters or {}).items():
        if col not in cols:
            return {"error": f"表 {table} 没有列 {col!r}", "available_columns": cols}
        where_sql.append(f"{col} = ?")
        params.append(val)

    if search:
        text_cols = [c for c in cols if c in ("name", "message", "note", "host")]
        if text_cols:
            where_sql.append("(" + " OR ".join(f"{c} LIKE ?" for c in text_cols) + ")")
            params += [f"%{search}%"] * len(text_cols)

    where = f"WHERE {' AND '.join(where_sql)}" if where_sql else ""
    limit = max(1, min(int(limit), 50))

    if aggregate:
        fn, _, col = aggregate.partition(":")
        fn = fn.strip().lower()
        if fn not in _AGG_FUNCS:
            return {"error": f"不支持的聚合 {fn!r}", "supported": list(_AGG_FUNCS)}
        if fn != "count":
            if col not in NUMERIC_COLUMNS.get(table, []):
                return {
                    "error": f"表 {table} 不能对 {col!r} 做 {fn}",
                    "numeric_columns": NUMERIC_COLUMNS.get(table, []),
                }
            expr = f"{fn.upper()}({col})"
        else:
            expr = "COUNT(*)"
        with _conn() as conn:
            row = conn.execute(
                f"SELECT {expr} AS result FROM {table} {where}",  # noqa: S608 - table/column whitelisted
                params,
            ).fetchone()
        return {"table": table, "aggregate": aggregate, "result": row["result"]}

    if order_by and order_by not in cols:
        return {"error": f"表 {table} 不能按 {order_by!r} 排序", "available_columns": cols}
    order = f"ORDER BY {order_by} {'DESC' if desc else 'ASC'}" if order_by else ""

    with _conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM {table} {where} {order} LIMIT ?",  # noqa: S608 - same as above
            (*params, limit),
        ).fetchall()

    return {
        "table": table,
        "count": len(rows),
        "rows": [_fmt_row(table, r) for r in rows],
    }


def schema_summary() -> str:
    """Table description shown to the model. Used by the tool description so tables are not described twice."""
    return "; ".join(f"{t}({', '.join(c)})" for t, c in TABLE_COLUMNS.items())


def seed_demo_business() -> int:
    """Seed demo business data so the tool path can be validated without a real data source.

    Deliberately **multi-table and foreign-key related** (alerts.host references
    hosts.name), because the value of "the database as a knowledge base" is precisely the
    relations: answering "which alerts does the slowest machine have" needs cross-table
    queries.

    After real ingestion is wired in, the ingestion script replaces this data; the schema
    does not change.
    """
    if query_metrics(limit=1):
        return 0

    now = time.time()
    for name, value, unit, note in [
        ("服务可用性", 99.95, "%", "最近 24 小时"),
        ("平均响应延迟", 187.0, "ms", "最近 1 小时"),
        ("错误率", 0.12, "%", "最近 1 小时"),
        ("活跃用户数", 3421.0, "人", "当前在线"),
    ]:
        record_metric(name, value, unit, note)

    hosts = [
        ("web-01", "10.0.1.11", "华东", 92.0, 41.0, "running"),
        ("web-02", "10.0.1.12", "华东", 431.0, 88.5, "degraded"),
        ("db-01", "10.0.2.21", "华北", 156.0, 63.2, "running"),
    ]
    alerts = [
        ("web-02", "critical", "CPU 使用率持续超过 85%", "firing"),
        ("web-02", "warning", "接口 P99 延迟超过 400ms", "firing"),
        ("db-01", "warning", "磁盘剩余空间不足 20%", "resolved"),
    ]
    orders = [
        ("A20261001001", "张三", "已发货", 299.0),
        ("A20261001002", "李四", "待发货", 88.5),
        ("A20261001003", "王五", "已完成", 1299.0),
    ]

    with _conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO hosts "
            "(name, ip, region, latency_ms, cpu_pct, status, created_at) VALUES (?,?,?,?,?,?,?)",
            [(*h, now) for h in hosts],
        )
        conn.executemany(
            "INSERT INTO alerts (host, level, message, status, created_at) VALUES (?,?,?,?,?)",
            [(*a, now) for a in alerts],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO orders "
            "(order_id, customer, status, amount, updated_at) VALUES (?,?,?,?,?)",
            [(*o, now) for o in orders],
        )

    total = 4 + len(hosts) + len(alerts) + len(orders)
    logger.info(f"[MEMORY] seeded {total} demo business rows (demo data, not real business)")
    return total
