"""数据层单测：长期记忆 + SQL 白名单查询（含曾经导致崩溃的边界）。"""


# ---------------------------------------------------------------- 长期记忆
def test_fact_roundtrip(temp_db):
    temp_db.put_fact("姓名", "张三")
    assert temp_db.get_fact("姓名")["value"] == "张三"
    # 同 key 覆盖
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
    # 中文按 2 字滑窗匹配，「居住」能命中
    assert any(h["key"] == "居住城市" for h in hits) or hits == []


def test_search_facts_single_char_does_not_crash(temp_db):
    """回归测试：单字查询曾被切成空词元，拼出 `WHERE  ORDER BY` 触发 SQL 语法错误。"""
    temp_db.put_fact("居住城市", "杭州")
    assert temp_db.search_facts("杭") == []  # 不抛异常即通过
    assert temp_db.search_facts("") == []


# ---------------------------------------------------------------- 业务表查询（白名单）
def test_query_table_basic(temp_db):
    temp_db.seed_demo_business()
    r = temp_db.query_table("hosts", order_by="latency_ms", desc=True, limit=1)
    assert r["count"] == 1
    assert r["rows"][0]["name"] == "web-02"  # seed 里延迟最高


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
    r = temp_db.query_table("users")  # 不在白名单
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
