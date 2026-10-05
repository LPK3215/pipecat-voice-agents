"""Tool handler unit tests: no LLM, no network -- drive the handler directly and inspect the
callback result.

Focus is on the two "silent failure" traps the docs keep stressing:
1. ``result_callback`` must carry ``run_llm=True`` (otherwise the tool runs but the bot never
   answers);
2. failure paths must also call back, not raise.

NOTE: the Chinese strings below are test data / expected spoken output -- do not translate.
"""

import asyncio
import json
from pathlib import Path

import pytest

import memory
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
        (sample_tools.device_schema("dev-test").handler, {"device": "空调", "action": "on"}),
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
    p = run(sample_tools.device_schema("dev-test").handler, {"device": "客厅灯", "action": "off"})
    assert p.result["ok"] is True and p.result["state"] == "关闭"


# ---------------------------------------------------------------- data lives in the data layer
def test_demo_rows_come_from_the_data_file_not_the_code():
    """Architecture guard: business rows belong to the data layer. A row re-appearing in the
    .py sources is exactly the anti-pattern this project must avoid (fake data hard-coded in
    the code while pretending to be a data layer)."""
    data = json.loads(memory.DEMO_DATA_FILE.read_text(encoding="utf-8"))
    sources = Path(memory.__file__).read_text(encoding="utf-8") + Path(
        sample_tools.__file__
    ).read_text(encoding="utf-8")
    values = [row["order_id"] for row in data["orders"]]
    values += [row["name"] for row in data["hosts"]]
    assert values
    for value in values:
        assert value not in sources, (
            f"{value!r} is hard-coded in the code; it belongs in {memory.DEMO_DATA_FILE.name}"
        )


def test_swapping_the_data_file_changes_answers_without_code_changes(tmp_path, monkeypatch):
    """The point of the separation: point the loader at a different file and the answers change
    with **no code edit** -- that is what makes the code and the data independent."""
    alt = tmp_path / "other-tools.json"
    alt.write_text(
        json.dumps(
            {
                "weather": {"火星": {"temperature_c": -60, "condition": "沙尘"}},
                "devices": ["月球车"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sample_tools, "DEMO_DATA_FILE", alt)
    sample_tools.load_demo_data(force=True)

    hit = run(sample_tools.get_weather, {"city": "火星"})
    assert hit.result["found"] is True and hit.result["temperature_c"] == -60
    assert run(sample_tools.get_weather, {"city": "杭州"}).result["found"] is False

    schema = sample_tools.device_schema("alt-session")
    assert "月球车" in schema.description  # the prompt follows the data as well
    assert run(schema.handler, {"device": "月球车", "action": "on"}).result["ok"] is True

    monkeypatch.undo()
    sample_tools.load_demo_data(force=True)  # back to the committed file for other tests


def test_device_state_is_per_session():
    """Same rule as reminders: stateful tools keep state per conversation, not per process."""
    run(sample_tools.device_schema("dev-a").handler, {"device": "客厅灯", "action": "on"})
    run(sample_tools.device_schema("dev-b").handler, {"device": "客厅灯", "action": "off"})
    assert sample_tools._device_state("dev-a")["客厅灯"] == "开启"
    assert sample_tools._device_state("dev-b")["客厅灯"] == "关闭"


# ---------------------------------------------------------------- per-session tool state
def test_reminder_state_is_per_session():
    """``set_reminder`` is the only stateful tool. With a shared store one conversation's
    reminders inflate another's count -- the same root cause as the module-level
    SESSION_ID bot.py used to have, so it gets its own test."""
    a = sample_tools.reminder_schema("session-a").handler
    b = sample_tools.reminder_schema("session-b").handler

    run(a, {"content": "开会", "when": "明天早上八点"})
    second = run(a, {"content": "买牛奶"})
    assert second.result["total_reminders"] == 2

    other = run(b, {"content": "交报告", "when": "周五"})
    assert other.result["total_reminders"] == 1  # not 3
    assert sample_tools.count_reminders("session-a") == 2  # untouched


def test_reminder_store_is_bounded():
    """The demo store keeps the newest entries instead of growing without limit."""
    handler = sample_tools.reminder_schema("session-cap").handler
    for i in range(sample_tools.MAX_REMINDERS_PER_SESSION + 5):
        run(handler, {"content": f"第{i}条"})
    assert sample_tools.count_reminders("session-cap") == sample_tools.MAX_REMINDERS_PER_SESSION


def test_build_tools_binds_reminder_to_the_session():
    """The tool is still exposed; only its state became per-session."""
    names = [t.name for t in tools.build_tools("session-x").standard_tools]
    assert "set_reminder" in names


# ---------------------------------------------------------------- long-term memory recall
def test_recall_fact_does_not_claim_nothing_while_facts_exist(temp_db):
    """Measured defect: a paraphrased query ("我住在哪") shares no characters with the stored
    key ("城市"), so the lexical search found nothing and the tool answered "没有相关的记录"
    while the fact was right there -- a user-visible wrong answer."""
    temp_db.put_fact("城市", "杭州")
    p = run(tools.recall_fact, {"query": "我住在哪"})
    assert p.result["found"] is False  # nothing matched those words...
    assert any(i["key"] == "城市" for i in p.result["items"])  # ...but we do remember it
    assert "杭州" in p.result["spoken"]


def test_recall_fact_still_reports_a_real_match(temp_db):
    temp_db.put_fact("城市", "杭州")
    p = run(tools.recall_fact, {"query": "城市"})
    assert p.result["found"] is True
    assert p.result["spoken"] == "城市是杭州"


def test_recall_fact_says_nothing_when_the_store_is_empty(temp_db):
    p = run(tools.recall_fact, {"query": "城市"})
    assert p.result["found"] is False
    assert p.result["spoken"] == "没有相关的记录"


# ---------------------------------------------------------------- failure must not be silent
def test_query_data_tolerates_a_bad_limit(temp_db):
    """Measured defect: ``limit="十条"`` raised ValueError inside the handler, so the tool
    "ran" while no result ever came back -- the bot never answered at all."""
    temp_db.seed_demo_business()
    p = run(tools.query_data, {"table": "alerts", "limit": "十条"})
    assert p.result.get("error") is None  # fell back to the default limit
    assert p.result["rows"]  # ...and the query still returned rows


def test_safe_handler_reports_instead_of_raising():
    """The wrapper is the safety net for the *unexpected*: without it the model gets no
    result at all and the bot stays silent."""

    async def boom(_params):
        raise RuntimeError("api down")

    p = FakeParams({})
    asyncio.run(tools.safe_handler(boom)(p))
    assert p.result["ok"] is False
    assert "api down" in p.result["error"]
    assert p.result["spoken"]  # so the model can answer honestly


def test_build_tools_wraps_every_handler():
    """Applied centrally in build_tools(), so a newly added tool cannot forget it."""
    schemas = tools.build_tools("session-x").standard_tools
    assert len(schemas) >= 12
    assert all(hasattr(s.handler, "__wrapped__") for s in schemas)


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
