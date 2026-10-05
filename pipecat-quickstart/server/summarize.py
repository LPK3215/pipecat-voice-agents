"""Context summarization: compress history as the conversation grows, to control tokens,
cost, and "attention drift".

Why it is needed:
    The context is re-sent every turn. As turns accumulate, tokens and cost grow linearly,
    and the model is more easily dragged off course by irrelevant history. The approach:
    compress the **older** messages into one summary while keeping the most recent
    originals verbatim.

Relation to the framework:
    pipecat ships ``LLMContextSummarizer`` (defaults: 8000 tokens / 20 unsummarized
    messages), but it is **not a FrameProcessor** and needs binding to the aggregator/worker.
    This module is an **equivalent, explicit, unit-testable** minimal implementation; if
    the official one is ever adopted, this module is the single replacement point.

Boundary (honest): summarization is lossy and only triggers above the threshold; the last
``keep_last`` messages always keep their original text, so recent topic details are safe.
"""

from __future__ import annotations

import asyncio
import os

from loguru import logger
from pipecat.frames.frames import LLMFullResponseEndFrame
from pipecat.observers.base_observer import BaseObserver

DEFAULT_MAX_MESSAGES = 20
DEFAULT_KEEP_LAST = 8

# NOTE: this prompt is intentionally Chinese -- the conversation is Chinese and the
# summary is injected back into the context.
SUMMARY_PROMPT = (
    "把下面这段对话压缩成一段简洁的中文摘要。要求：保留关键事实、决定、结论与用户偏好；"
    "人名、时间、数值、订单号等不要遗漏；不要加入原文没有的内容。只输出摘要正文。"
)
SUMMARY_PREFIX = "[earlier conversation summary] "


def _role(msg) -> str:
    if isinstance(msg, dict):
        return str(msg.get("role", "?"))
    return str(getattr(msg, "role", "?"))


def _content(msg) -> str:
    if isinstance(msg, dict):
        return str(msg.get("content", ""))
    return str(getattr(msg, "content", ""))


def _render(messages) -> str:
    return "\n".join(f"{_role(m)}: {_content(m)}" for m in messages)


def summarize_context(
    context,
    llm_call,
    *,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    keep_last: int = DEFAULT_KEEP_LAST,
) -> bool:
    """Compress older messages into one summary message when above the threshold.

    Returns whether a compaction happened. ``llm_call(messages) -> str`` is injected by
    the caller (real call or a test double).
    """
    messages = list(getattr(context, "messages", None) or [])
    if len(messages) <= max_messages:
        return False

    head, tail = messages[:-keep_last], messages[-keep_last:]
    # system messages (identity and rules) are not summarized; they stay at the head as-is.
    system = [m for m in head if _role(m) == "system"]
    body = [m for m in head if _role(m) != "system"]
    if not body:
        return False

    try:
        summary = llm_call(
            [{"role": "user", "content": f"{SUMMARY_PROMPT}\n\n{_render(body)}"}]
        )
    except Exception as exc:  # noqa: BLE001 - a failed summary must not break the dialog
        logger.warning(f"[SUMMARY] summarization failed, skipping this time: {type(exc).__name__}: {exc}")
        return False

    summary = (summary or "").strip()
    if not summary:
        logger.warning("[SUMMARY] empty summary, skipping")
        return False

    context.set_messages(
        [*system, {"role": "user", "content": f"{SUMMARY_PREFIX}{summary}"}, *tail]
    )
    logger.info(
        f"[SUMMARY] context compacted: {len(messages)} -> {len(system) + 1 + len(tail)} messages"
    )
    return True


def make_llm_call():
    """Make a direct summarization call using the current provider config (bypassing the pipeline)."""
    from openai import OpenAI

    from settings import llm_config

    cfg = llm_config()
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    model = os.getenv("SUMMARY_MODEL") or cfg["model"]

    def call(messages) -> str:
        resp = client.chat.completions.create(
            model=model, messages=messages, max_tokens=512, temperature=0.3
        )
        return resp.choices[0].message.content or ""

    return call


class ContextSummarizer(BaseObserver):
    """Check context length after each assistant reply and compact asynchronously above the threshold."""

    def __init__(self, context, llm_call=None, **kwargs) -> None:
        kwargs.setdefault("observe_every_push", False)
        super().__init__(**kwargs)
        self._context = context
        self._llm_call = llm_call
        self._busy = False
        self.compactions = 0

    async def _maybe_summarize(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            if self._llm_call is None:
                self._llm_call = make_llm_call()
            if await asyncio.to_thread(summarize_context, self._context, self._llm_call):
                self.compactions += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[SUMMARY] exception: {type(exc).__name__}: {exc}")
        finally:
            self._busy = False

    async def on_push_frame(self, data) -> None:
        if isinstance(data.frame, LLMFullResponseEndFrame):
            self.create_task(self._maybe_summarize(), name="context-summarize")
