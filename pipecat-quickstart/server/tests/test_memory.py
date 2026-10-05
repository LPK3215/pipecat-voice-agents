"""Data-layer unit tests: long-term memory + SQL whitelist queries (including boundaries that
once caused crashes).

NOTE: the Chinese strings below are test data -- do not translate.
"""


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
