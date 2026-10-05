"""Trust guard: detect "claimed but never executed" answers.

Why this exists (measured in HANDBOOK-02 section 5):
    When the model does NOT call a tool, it does not say "I cannot do that" -- it
    fabricates a success, e.g. "Sure, I set a reminder for 8am tomorrow" while nothing
    happened. In a voice setting the user cannot see any UI feedback and has to trust
    what is spoken -- the most dangerous failure mode, because the user then makes
    decisions based on a false state (thinking a reminder is set, a light is off,
    a notification was sent).

How it works: a **BaseObserver** (same idea as memory.TurnRecorder, position-independent).
Split in two so it can be unit-tested:
    ``detect_false_claim(text, executed)`` -- pure function, easy to test, no framework
    ``HallucinationGuard(BaseObserver)``     -- collects this turn's tool calls and reply
                                                text, decides at the end of the reply

Honest about its limits:
    This is **heuristic** (action wording + the tool that should have run). It will miss
    some phrasings and may false-positive. Its value is turning a "silent lie" into an
    "observable alert"; the strongest constraint belongs in the orchestration layer
    (flows, forcing write operations through a confirmation flow). Detection first is the
    right trade-off at this stage.
"""

from __future__ import annotations

import re

from loguru import logger
from pipecat.frames.frames import (
    FunctionCallResultFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.observers.base_observer import BaseObserver

# Rule: "completed an action" wording in the reply -> if none of these tools were called,
# treat it as a suspected false claim.
#
# Pattern shape: "completion marker + gap + action word". Chinese has many phrasings
# ("已为你设置...", "已经把...关掉了", "已经记住...了"), so the gap allows any characters
# that are not sentence-ending punctuation.
# This is heuristic; err on the loose side (better to over-report than to miss the most
# dangerous lie). False positives are flagged as [GUARD] in the log.
#
# NOTE: the regexes intentionally match Chinese text -- that is functional, not output.
_DONE = r"(?:已经|已|完成|搞定|成功)"
_GAP = r"[^。！？!?\n]{0,20}"

ACTION_RULES: tuple[tuple[re.Pattern[str], frozenset[str], str], ...] = (
    (re.compile(rf"{_DONE}{_GAP}提醒"), frozenset({"set_reminder"}), "set_reminder"),
    (
        re.compile(rf"{_DONE}{_GAP}(?:关掉|关闭|打开|开启)"),
        frozenset({"control_device"}),
        "control_device",
    ),
    (
        re.compile(rf"{_DONE}{_GAP}(?:记住|记下|记录)"),
        frozenset({"remember_fact"}),
        "remember_fact",
    ),
    (
        re.compile(rf"{_DONE}{_GAP}(?:通知|发送|发出)"),
        frozenset({"send_notification"}),
        "send_notification",
    ),
)


def detect_false_claim(text: str, executed: frozenset[str] | set[str]) -> str | None:
    """Return the action label if the reply claims an action with no matching tool call.

    Pure function (no framework dependency) so it is easy to unit-test.
    """
    content = (text or "").strip()
    if not content:
        return None
    executed = set(executed)
    for pattern, tools, label in ACTION_RULES:
        if pattern.search(content) and not (tools & executed):
            return label
    return None


class HallucinationGuard(BaseObserver):
    """Observer that turns "claimed but never executed" into a visible alert.

    Args:
        on_violation: optional ``async (label, executed) -> None`` callback, used to
            notify the frontend or trigger a re-answer. If omitted, only logs.
    """

    def __init__(self, on_violation=None, **kwargs) -> None:
        kwargs.setdefault("observe_every_push", False)
        super().__init__(**kwargs)
        self._text: list[str] = []
        self._executed: set[str] = set()
        self._on_violation = on_violation
        self.violations = 0

    def set_on_violation(self, callback) -> None:
        """Attach the callback after the worker exists (it needs worker.queue_frames)."""
        self._on_violation = callback

    @staticmethod
    def _succeeded(frame: FunctionCallResultFrame) -> bool:
        """Whether the tool "succeeded". Failures may be reported as result={"ok": False}."""
        result = getattr(frame, "result", None)
        return not (isinstance(result, dict) and result.get("ok") is False)

    async def on_push_frame(self, data) -> None:
        frame = data.frame

        if isinstance(frame, LLMFullResponseStartFrame):
            self._text = []
            self._executed = set()
        elif isinstance(frame, FunctionCallResultFrame):
            if self._succeeded(frame):
                self._executed.add(frame.function_name)
        elif isinstance(frame, LLMTextFrame):
            self._text.append(getattr(frame, "text", "") or "")
        elif isinstance(frame, LLMFullResponseEndFrame):
            label = detect_false_claim("".join(self._text), self._executed)
            if label:
                self.violations += 1
                logger.error(
                    f"[GUARD] suspected false claim: reply claims '{label}' "
                    f"but successful tool calls this turn were "
                    f"{sorted(self._executed) or 'none'}"
                )
                if self._on_violation is not None:
                    await self._on_violation(label, sorted(self._executed))
