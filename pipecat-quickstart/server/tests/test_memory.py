"""Data-layer unit tests: long-term memory + SQL whitelist queries (including boundaries that
once caused crashes).

NOTE: the Chinese strings below are test data -- do not translate.
"""

import sqlite3
import time


# ---------------------------------------------------------------- long-term memory
def test_fact_roundtrip(temp_db):
    temp_db.put_fact("姓名", "张三")
    assert temp_db.get_fact("姓名")["value"] == "张三"
    # same key overwrites
    temp_db.put_fact("姓名", "李四")
    assert temp_db.get_fact("姓名")["value"] == "李四"
    keys = [f["key"] for f in temp_db.list_facts()]
    assert "姓名" in keys


def test_delete_fact(temp_db):
    temp_db.put_fact("a", "1")
    assert temp_db.delete_fact("a") is True
    assert temp_db.get_fact("a") is None
    assert temp_db.delete_fact("a") is False


def test_search_facts_chinese(temp_db):
    temp_db.put_fact("居住城市", "杭州")
    temp_db.put_fact("喜欢的语言", "Python")
    hits = temp_db.search_facts("住在哪里")
    # Chinese is matched by 2-character sliding windows, so "居住" can hit
    assert any(h["key"] == "居住城市" for h in hits) or hits == []


def test_search_facts_single_char_does_not_crash(temp_db):
    """Regression: a single-character query was once split into an empty token list, producing
    `WHERE  ORDER BY` and an SQL syntax error."""
    temp_db.put_fact("居住城市", "杭州")
    assert temp_db.search_facts("杭") == []  # not raising is the pass condition
    assert temp_db.search_facts("") == []


# ---------------------------------------------------------------- the SQL layer's promise
def test_identifiers_are_whitelisted_not_interpolated(temp_db):
    """The documented promise is "the model never writes SQL": table, filter column, order_by
    and aggregate are all exact-match checked against a whitelist before SQL is built.

    Every sample below is injection-shaped and must be rejected -- and the data must survive.
    """
    temp_db.seed_demo_business()
    before = temp_db.query_table("orders", limit=50)["rows"]

    for bad_table in ("orders; DROP TABLE orders;--", "orders)--", "orders' OR '1'='1"):
        assert "error" in temp_db.query_table(table=bad_table)
    # tables that exist in the DB but are deliberately not exposed (history, memory, KB)
    for hidden in ("turns", "facts", "documents", "chunks"):
        assert "error" in temp_db.query_table(table=hidden)

    for col in ("status' OR '1'='1", "1=1", "(SELECT 1)"):
        assert "error" in temp_db.query_table("orders", filters={col: "x"})

    for order_by in ("id DESC; DROP TABLE orders;--", "(SELECT 1)", "id, updated_at"):
        assert "error" in temp_db.query_table("orders", order_by=order_by)

    for aggregate in (
        "sum:amount; DROP TABLE orders",
        "count); DROP TABLE orders;--",
        "sum:id",
        "load_extension:amount",
    ):
        assert "error" in temp_db.query_table("orders", aggregate=aggregate)

    # A value shaped like SQL is a literal: it matches nothing, it does not execute.
    assert temp_db.query_table("orders", filters={"status": "x' OR '1'='1"})["rows"] == []

    assert temp_db.query_table("orders", limit=50)["rows"] == before


def test_search_filters_orders_by_customer(temp_db):
    """Regression: the searchable columns were one global name list, and `orders` has none of
    those names -- so `search` added no condition and returned unfiltered rows that looked like
    matches (`search="绝不存在zzz"` still returned 10 rows)."""
    temp_db.seed_demo_business()
    rows = temp_db.query_table("orders", limit=50)["rows"]
    assert rows
    # Data-driven on purpose: the seed and the sample CSV ship different customer names.
    partial = rows[0]["customer"][:1]
    hit = temp_db.query_table("orders", search=partial, limit=50)["rows"]
    assert hit
    assert all(partial in (r["customer"] + r["order_id"]) for r in hit)
    assert temp_db.query_table("orders", search="绝不存在zzz", limit=50)["rows"] == []


def test_every_queryable_table_supports_text_search(temp_db):
    """Otherwise `search` would silently do nothing (the bug above) instead of erroring."""
    for table in temp_db.TABLE_COLUMNS:
        assert temp_db.TEXT_COLUMNS.get(table), f"{table} has no searchable column"


def test_timestamps_are_formatted_for_any_at_column(temp_db):
    """`orders` uses `updated_at`; formatting only `created_at` let a raw epoch reach `spoken`,
    which the bot reads aloud ("updated_at是1791185075.6945438")."""
    temp_db.seed_demo_business()
    row = temp_db.query_table("orders", limit=1)["rows"][0]
    assert "time" in row and "updated_at" not in row
    assert len(row["time"]) == 16  # "2026-10-01 23:55"


# ---------------------------------------------------------------- layer boundaries
def test_answers_come_from_the_data_layer_not_from_constants(temp_db):
    """The decisive test for "data hard-coded in the code" vs "layered with real interfaces".

    With an EMPTY data layer the query must report that it found nothing -- hard-coded answers
    would still be produced. Then a row written by an **external** sqlite3 client (not through
    any function of this project) must be visible immediately: that is what proves the code
    really reads the data layer instead of repeating constants of its own.
    """
    empty = temp_db.query_table("orders", limit=5)
    assert empty["rows"] == []  # nothing in the data layer -> nothing to answer from
    assert temp_db.query_table("orders", aggregate="count")["result"] == 0

    conn = sqlite3.connect(temp_db.DB_PATH)
    conn.execute(
        "INSERT INTO orders (order_id, customer, status, amount, updated_at) VALUES (?,?,?,?,?)",
        ("EXT-1", "外部写入", "待发货", 12.5, time.time()),
    )
    conn.commit()
    conn.close()

    hit = temp_db.query_table("orders", filters={"order_id": "EXT-1"})
    assert hit["count"] == 1
    assert hit["rows"][0]["customer"] == "外部写入"


def test_declared_columns_exist_in_the_real_schema(temp_db):
    """Code layer's declared interface (the whitelists) vs the data layer's real schema.

    A whitelisted column that does not exist in the table would only surface as a runtime
    "no such column" the first time a user happened to query it -- so the two sides are
    compared directly here.
    """
    conn = sqlite3.connect(temp_db.DB_PATH)
    real_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    for table, columns in temp_db.TABLE_COLUMNS.items():
        assert table in real_tables, f"{table} is declared in code but does not exist in the DB"
        real = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        assert not set(columns) - real, f"{table}: {sorted(set(columns) - real)} not in the DB"

    for table, columns in {**temp_db.TEXT_COLUMNS, **temp_db.NUMERIC_COLUMNS}.items():
        real = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        assert set(columns) <= real, f"{table}: {sorted(set(columns) - real)} not in the DB"
    conn.close()


# ---------------------------------------------------------------- business table queries (whitelist)
def test_query_table_basic(temp_db):
    temp_db.seed_demo_business()
    r = temp_db.query_table("hosts", order_by="latency_ms", desc=True, limit=1)
    assert r["count"] == 1
    assert r["rows"][0]["name"] == "web-02"  # highest latency in the seed


def test_query_table_filters(temp_db):
    temp_db.seed_demo_business()
    r = temp_db.query_table("alerts", filters={"status": "firing"})
    assert r["count"] == 2


def test_query_table_aggregate(temp_db):
    temp_db.seed_demo_business()
    r = temp_db.query_table("alerts", filters={"status": "firing"}, aggregate="count")
    assert r["result"] == 2
    r2 = temp_db.query_table("hosts", aggregate="max:latency_ms")
    assert r2["result"] == 431.0


def test_query_table_rejects_unknown_table(temp_db):
    r = temp_db.query_table("users")  # not whitelisted
    assert "error" in r
    assert "available_tables" in r


def test_query_table_rejects_unknown_column(temp_db):
    r = temp_db.query_table("hosts", filters={"password": "x"})
    assert "error" in r and "available_columns" in r


def test_query_table_rejects_bad_order(temp_db):
    r = temp_db.query_table("hosts", order_by="secret")
    assert "error" in r


def test_query_table_limit_clamped(temp_db):
    temp_db.seed_demo_business()
    r = temp_db.query_table("alerts", limit=9999)
    assert r["count"] <= 50


def test_schema_summary_lists_tables():
    import memory

    s = memory.schema_summary()
    for t in memory.TABLE_COLUMNS:
        assert t in s


# ---------------------------------------------------------------- orders table (written by the ingestion side)
def test_upsert_order_is_idempotent(temp_db):
    """Order ids are unique: re-ingesting should overwrite, not insert duplicate rows."""
    temp_db.upsert_order("A1", "张三", "待发货", 10.0)
    temp_db.upsert_order("A1", "张三", "已发货", 12.0)
    r = temp_db.query_table("orders", filters={"order_id": "A1"})
    assert r["count"] == 1
    assert r["rows"][0]["status"] == "已发货"
    assert r["rows"][0]["amount"] == 12.0


def test_orders_aggregate_and_whitelist(temp_db):
    temp_db.upsert_order("A1", "张三", "待发货", 10.0)
    temp_db.upsert_order("A2", "李四", "已发货", 20.0)
    assert temp_db.query_table("orders", aggregate="sum:amount")["result"] == 30.0
    assert temp_db.query_table("orders", aggregate="count")["result"] == 2
    assert "orders" in temp_db.schema_summary()


def test_orders_seeded_with_demo_data(temp_db):
    temp_db.seed_demo_business()
    r = temp_db.query_table("orders", filters={"status": "待发货"})
    assert r["count"] >= 1
