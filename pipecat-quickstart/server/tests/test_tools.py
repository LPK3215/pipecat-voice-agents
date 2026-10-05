"""Tool handler unit tests: no LLM, no network -- drive the handler directly and inspect the
callback result.

Focus is on the two "silent failure" traps the docs keep stressing:
1. ``result_callback`` must carry ``run_llm=True`` (otherwise the tool runs but the bot never
   answers);
2. failure paths must also call back, not raise.

NOTE: the Chinese strings below are test data / expected spoken output -- do not translate.
"""

import asyncio

import pytest

import sample_tools
import tools


class FakeParams:
    """Minimal FunctionCallParams stand-in: only the two members a handler uses."""

    def __init__(self, arguments=None):
        self.arguments = arguments or {}
        self.result = None
        self.properties = None

    async def result_callback(self, result, properties=None):
        self.result = result
        self.properties = properties


def run(handler, arguments=None):
    p = FakeParams(arguments)
    asyncio.run(handler(p))
    return p


# ---------------------------------------------------------------- key traps
def test_result_props_triggers_next_llm():
    """run_llm defaults to None (falsy), so it must be explicitly True or no answer follows."""
    assert tools._RESULT_PROPS.run_llm is True
    assert sample_tools._RESULT_PROPS.run_llm is True


def test_every_handler_passes_result_props():
    """Every handler should pass properties carrying run_llm on success."""
    cases = [
        (tools.get_current_time, {}),
        (sample_tools.get_weather, {"city": "杭州"}),
        (sample_tools.calculate, {"expression": "1+1"}),
        (sample_tools.convert_unit, {"value": 1, "from_unit": "米", "to_unit": "厘米"}),
        (sample_tools.control_device, {"device": "空调", "action": "on"}),
    ]
    for handler, args in cases:
        p = run(handler, args)
        assert p.properties is not None, f"{handler.__name__} did not pass properties"
        assert p.properties.run_llm is True, f"{handler.__name__} did not enable run_llm"


# ---------------------------------------------------------------- system / calc / convert
def test_get_current_time():
    p = run(tools.get_current_time, {})
    assert p.result["spoken"].startswith("现在是")
    assert "iso" in p.result


def test_calculate_ok():
    p = run(sample_tools.calculate, {"expression": "(23*17+5)/2"})
    assert p.result["ok"] is True
    assert p.result["result"] == 198


def test_calculate_rejects_unsafe_expression():
    """The expression comes from the model, so anything beyond arithmetic must be rejected (never eval)."""
    p = run(sample_tools.calculate, {"expression": "__import__('os').getcwd()"})
    assert p.result["ok"] is False


def test_convert_temperature():
    p = run(sample_tools.convert_unit, {"value": 30, "from_unit": "摄氏度", "to_unit": "华氏度"})
    assert p.result["ok"] is True
    assert p.result["result"] == 86.0


def test_convert_length_unsupported_pair():
    p = run(sample_tools.convert_unit, {"value": 1, "from_unit": "米", "to_unit": "千克"})
    assert p.result["ok"] is False


# ---------------------------------------------------------------- sample tools
def test_get_weather_hit_and_miss():
    hit = run(sample_tools.get_weather, {"city": "杭州市"})  # the "市" suffix should be normalized
    assert hit.result["found"] is True and hit.result["city"] == "杭州"
    miss = run(sample_tools.get_weather, {"city": "火星"})
    assert miss.result["found"] is False


def test_control_device():
    p = run(sample_tools.control_device, {"device": "客厅灯", "action": "off"})
    assert p.result["ok"] is True and p.result["state"] == "关闭"


# ---------------------------------------------------------------- memory tools
def test_remember_and_recall(temp_db):
    r = run(tools.remember_fact, {"key": "姓名", "value": "张三"})
    assert r.result["ok"] is True
    assert temp_db.get_fact("姓名")["value"] == "张三"

    q = run(tools.recall_fact, {"query": "姓名"})
    assert q.result["found"] is True


def test_remember_fact_rejects_empty(temp_db):
    r = run(tools.remember_fact, {"key": "", "value": ""})
    assert r.result["ok"] is False


# ---------------------------------------------------------------- structured query tool
def test_query_data_aggregate(temp_db):
    temp_db.seed_demo_business()
    p = run(tools.query_data, {"table": "alerts", "filters": {"status": "firing"}, "aggregate": "count"})
    assert p.result["result"] == 2
    assert "2" in p.result["spoken"]


def test_query_data_filters_as_json_string(temp_db):
    """Some models pass filters as a JSON string; the handler must accept that."""
    temp_db.seed_demo_business()
    p = run(tools.query_data, {"table": "alerts", "filters": '{"status": "firing"}'})
    assert p.result["count"] == 2


# ---------------------------------------------------------------- registry
def test_build_tools_excludes(monkeypatch):
    monkeypatch.setenv("TOOLS_EXCLUDE", "get_weather,calculate")
    names = [t.name for t in tools.build_tools().standard_tools]
    assert "get_weather" not in names
    assert "calculate" not in names
    assert "get_current_time" in names
