"""Unit tests for the trust guard: rule matching + observer decision.

No framework runtime, no network. The Chinese text in the cases below is
**test input** (the guard matches Chinese replies) and is intentionally kept.
"""

import asyncio

import pytest

from guards import HallucinationGuard, detect_false_claim


@pytest.mark.parametrize(
    "text,executed,expect",
    [
        # Claims an action without calling the tool -> hit (false claim)
        ("好的，已为你设置明天早上八点的提醒。", set(), "set_reminder"),
        ("已经把客厅灯关掉了。", set(), "control_device"),
        ("已经记住你叫张三了。", set(), "remember_fact"),
        ("已经给你发了通知。", set(), "send_notification"),
        # Called the matching tool -> no false positive
        ("好的，已为你设置明天早上八点的提醒。", {"set_reminder"}, None),
        ("已经把客厅灯关掉了。", {"control_device"}, None),
        ("已经记住你叫张三了。", {"remember_fact"}, None),
        ("已经给你发了通知。", {"send_notification"}, None),
        # No action wording at all
        ("你好，我是语音助手，有什么可以帮你？", set(), None),
        ("现在几点了？", set(), None),
        ("", set(), None),
    ],
)
def test_detect_false_claim(text, executed, expect):
    assert detect_false_claim(text, executed) == expect


def test_other_tool_does_not_count():
    """Calling a **different** tool does not count: claims a device switch, only called time."""
    assert detect_false_claim("已经把空调打开了。", {"get_current_time"}) == "control_device"


def test_succeeded_helper():
    from pipecat.frames.frames import FunctionCallResultFrame

    ok = FunctionCallResultFrame(
        function_name="a", tool_call_id="t", arguments={}, result={"ok": True}
    )
    bad = FunctionCallResultFrame(
        function_name="a", tool_call_id="t", arguments={}, result={"ok": False}
    )
    plain = FunctionCallResultFrame(
        function_name="a", tool_call_id="t", arguments={}, result={"found": True}
    )
    assert HallucinationGuard._succeeded(ok) is True
    assert HallucinationGuard._succeeded(bad) is False  # explicit failure is not a success
    assert HallucinationGuard._succeeded(plain) is True


class _Data:
    """Minimal FramePushed stand-in (the observer only uses .frame)."""

    def __init__(self, frame):
        self.frame = frame


def test_guard_observer_end_to_end():
    from pipecat.frames.frames import (
        FunctionCallResultFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )

    hits: list[str] = []

    async def on_violation(label, executed):
        hits.append(label)

    guard = HallucinationGuard(on_violation=on_violation)

    async def drive(with_tool: bool):
        # One turn: claims a reminder, with/without calling set_reminder.
        await guard.on_push_frame(_Data(LLMFullResponseStartFrame()))
        if with_tool:
            await guard.on_push_frame(
                _Data(
                    FunctionCallResultFrame(
                        function_name="set_reminder",
                        tool_call_id="t1",
                        arguments={},
                        result={"ok": True},
                    )
                )
            )
        await guard.on_push_frame(_Data(LLMTextFrame(text="好的，已经帮你设置好提醒了。")))
        await guard.on_push_frame(_Data(LLMFullResponseEndFrame()))

    asyncio.run(drive(with_tool=False))
    assert hits == ["set_reminder"]
    assert guard.violations == 1

    # Second turn: the tool really was called -> no hit, and per-turn state resets.
    asyncio.run(drive(with_tool=True))
    assert hits == ["set_reminder"]
    assert guard.violations == 1
