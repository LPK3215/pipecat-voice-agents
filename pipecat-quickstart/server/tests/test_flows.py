"""Explicit-orchestration unit tests: chain ordering + argument passing + composite tool behavior.

NOTE: the Chinese strings below are test data -- do not translate.
"""

import asyncio

import pytest


def _run(args=None):
    """Minimal FunctionCallParams stand-in."""

    class _P:
        def __init__(self):
            self.arguments = args or {}
            self.result = None
            self.properties = None

        async def result_callback(self, result, properties=None):
            self.result = result
            self.properties = properties

    return _P()


# ---------------------------------------------------------------- basic chain
def test_run_steps_orders_and_passes_previous():
    from flows import run_steps

    calls = []

    async def step_a(params):
        calls.append(("a", params.arguments))
        await params.result_callback({"value": "杭州"})

    async def step_b(params):
        calls.append(("b", params.arguments))
        await params.result_callback({"ok": True})

    async def main():
        return await run_steps(
            [
                (step_a, {"query": "城市"}),
                (step_b, lambda prev: {"city": prev[0]["value"]}),
            ]
        )

    results = asyncio.run(main())
    # Order is fixed: a before b, and b received a's result
    assert [c[0] for c in calls] == ["a", "b"]
    assert calls[1][1] == {"city": "杭州"}
    assert results[-1] == {"ok": True}


def test_first_value_handles_nested():
    from flows import first_value

    assert first_value({"a": 1}, "a") == 1
    assert first_value([{"v": "x"}, {"v": "y"}], "v") == "x"
    assert first_value([], "v") is None
    assert first_value(None, "v") is None


# ---------------------------------------------------------------- composite tool
def test_my_local_weather_uses_remembered_city(temp_db):
    """Core case: with no city given, it must recall first (no longer inventing "Beijing")."""
    import tools

    temp_db.put_fact("城市", "杭州")
    p = _run()
    asyncio.run(tools.my_local_weather(p))
    assert p.result["found"] is True
    assert p.result["city"] == "杭州"          # came from recall_fact, not invented by the model
    assert len(p.result["steps"]) == 2          # both steps ran


def test_my_local_weather_without_memory_asks_user(temp_db):
    import tools

    p = _run()
    asyncio.run(tools.my_local_weather(p))
    assert p.result["found"] is False
    assert "城市" in p.result["spoken"]


def test_local_weather_registered():
    import tools

    names = [t.name for t in tools.build_tools().standard_tools]
    assert "my_local_weather" in names
