"""Unit tests for the observers that persist / inspect the conversation.

These pin down the components that had failed **silently**: ``TurnRecorder`` used to be
attached as a pipeline processor and never wrote a single row, and ``ContextSummarizer``
was never exercised at all. Both are driven here with fake frames, offline.

NOTE: the Chinese strings below are test data -- do not translate.
"""

import asyncio


class _Data:
    """Minimal FramePushed stand-in (observers only read ``.frame``)."""

    def __init__(self, frame):
        self.frame = frame


def _drive(observer, frames):
    async def _run():
        for f in frames:
            await observer.on_push_frame(_Data(f))

    asyncio.run(_run())


# ---------------------------------------------------------------- TurnRecorder
def test_turn_recorder_persists_both_sides(temp_db):
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
        TranscriptionFrame,
    )

    import memory

    rec = memory.TurnRecorder("s1")
    _drive(
        rec,
        [
            TranscriptionFrame(text="你好", user_id="u", timestamp="t"),
            LLMFullResponseStartFrame(),
            LLMTextFrame(text="你好，"),
            LLMTextFrame(text="我是助手。"),
            LLMFullResponseEndFrame(),
        ],
    )

    rows = temp_db.recent_turns("s1", limit=10)
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "你好"
    # A streamed reply must land as ONE row, not one row per text frame.
    assert rows[1]["content"] == "你好，我是助手。"


def test_turn_recorder_records_text_channel(temp_db):
    """The RTVI send-text path arrives as LLMMessagesAppendFrame, not TranscriptionFrame."""
    from pipecat.frames.frames import LLMMessagesAppendFrame

    import memory

    rec = memory.TurnRecorder("s2")
    _drive(rec, [LLMMessagesAppendFrame(messages=[{"role": "user", "content": "键盘提问"}])])

    assert temp_db.recent_turns("s2") == [{"role": "user", "content": "键盘提问"}]


def test_turn_recorder_skips_empty_reply(temp_db):
    from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame

    import memory

    rec = memory.TurnRecorder("s3")
    _drive(rec, [LLMFullResponseStartFrame(), LLMFullResponseEndFrame()])

    assert temp_db.recent_turns("s3") == []


def test_turn_recorder_start_frame_resets_buffer(temp_db):
    """A turn interrupted mid-reply must not leak its half-sentence into the next turn."""
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )

    import memory

    rec = memory.TurnRecorder("s4")
    _drive(rec, [LLMFullResponseStartFrame(), LLMTextFrame(text="被打断的半句")])
    _drive(rec, [LLMFullResponseStartFrame(), LLMTextFrame(text="新回答"), LLMFullResponseEndFrame()])

    rows = temp_db.recent_turns("s4")
    assert [r["content"] for r in rows] == ["新回答"]


# ---------------------------------------------------------------- ContextSummarizer
def test_summarizer_compacts_over_threshold():
    from summarize import SUMMARY_PREFIX, ContextSummarizer

    class FakeContext:
        def __init__(self, n):
            self.messages = [{"role": "system", "content": "你是助手"}] + [
                {"role": "user", "content": f"第{i}条"} for i in range(n)
            ]

        def set_messages(self, messages):
            self.messages = list(messages)

    ctx = FakeContext(30)  # 31 messages > default threshold of 20
    summarizer = ContextSummarizer(ctx, llm_call=lambda _msgs: "摘要正文")
    asyncio.run(summarizer._maybe_summarize())

    assert summarizer.compactions == 1
    assert any(SUMMARY_PREFIX in str(m.get("content", "")) for m in ctx.messages)
    assert len(ctx.messages) < 31


def test_summarizer_noop_below_threshold():
    from summarize import ContextSummarizer

    class FakeContext:
        def __init__(self):
            self.messages = [{"role": "user", "content": "只有一条"}]

        def set_messages(self, messages):
            self.messages = list(messages)

    ctx = FakeContext()
    summarizer = ContextSummarizer(ctx, llm_call=lambda _msgs: "摘要正文")
    asyncio.run(summarizer._maybe_summarize())

    assert summarizer.compactions == 0
    assert len(ctx.messages) == 1


def test_summarizer_survives_llm_failure():
    from summarize import ContextSummarizer

    class FakeContext:
        def __init__(self):
            self.messages = [{"role": "user", "content": f"第{i}条"} for i in range(30)]

        def set_messages(self, messages):
            self.messages = list(messages)

    def boom(_msgs):
        raise RuntimeError("api down")

    ctx = FakeContext()
    summarizer = ContextSummarizer(ctx, llm_call=boom)
    asyncio.run(summarizer._maybe_summarize())

    assert summarizer.compactions == 0
    assert len(ctx.messages) == 30  # untouched


# ---------------------------------------------------------------- memory injection
class _Ctx:
    def __init__(self):
        self.messages = []

    def add_message(self, msg):
        self.messages.append(msg)


def test_load_memory_injects_facts_and_previous_turns(temp_db):
    import memory

    memory.put_fact("姓名", "张三")
    memory.save_turn("old-session", "user", "上次我们聊到部署")
    memory.save_turn("old-session", "assistant", "好的")

    ctx = _Ctx()
    assert memory.load_memory_into_context(ctx, limit=5) == 2

    text = "\n".join(m["content"] for m in ctx.messages)
    assert "张三" in text
    assert "上次我们聊到部署" in text


def test_load_memory_honors_limit_and_empty_db(temp_db):
    import memory

    ctx = _Ctx()
    assert memory.load_memory_into_context(ctx) == 0  # nothing stored yet

    for i in range(10):
        memory.put_fact(f"k{i}", f"v{i}")

    ctx = _Ctx()
    memory.load_memory_into_context(ctx, limit=2)
    fact_lines = [ln for ln in ctx.messages[0]["content"].splitlines() if ln.startswith("- ")]
    assert len(fact_lines) == 2
