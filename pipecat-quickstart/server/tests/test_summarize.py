"""Context summarization unit tests: threshold, tail retention, system retention, and failures
not affecting the main flow.

NOTE: the Chinese strings below are test data -- do not translate.
"""

from summarize import SUMMARY_PREFIX, summarize_context


class FakeContext:
    def __init__(self, messages):
        self.messages = list(messages)

    def set_messages(self, messages):
        self.messages = list(messages)


def _msgs(n, system=False):
    out = []
    if system:
        out.append({"role": "system", "content": "你是助手"})
    for i in range(n):
        out.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"第{i}条"})
    return out


def test_no_action_under_threshold():
    ctx = FakeContext(_msgs(5))
    called = []
    assert summarize_context(ctx, lambda m: called.append(m) or "x", max_messages=20) is False
    assert called == []
    assert len(ctx.messages) == 5


def test_compresses_and_keeps_tail_and_system():
    msgs = _msgs(30, system=True)  # 31 messages
    ctx = FakeContext(msgs)
    changed = summarize_context(ctx, lambda m: "摘要正文", max_messages=20, keep_last=8)
    assert changed is True

    roles = [m["role"] for m in ctx.messages]
    # the original system message stays at the head
    assert ctx.messages[0]["role"] == "system"
    # a summary message was inserted
    assert any(SUMMARY_PREFIX in str(m.get("content", "")) for m in ctx.messages)
    # the last 8 messages keep their original text
    assert [m["content"] for m in ctx.messages[-8:]] == [m["content"] for m in msgs[-8:]]
    # total message count dropped
    assert len(ctx.messages) < len(msgs)


def test_llm_failure_is_swallowed():
    ctx = FakeContext(_msgs(30))

    def boom(_):
        raise RuntimeError("api down")

    assert summarize_context(ctx, boom, max_messages=20) is False
    assert len(ctx.messages) == 30  # unchanged


def test_empty_summary_keeps_context():
    ctx = FakeContext(_msgs(30))
    assert summarize_context(ctx, lambda m: "   ", max_messages=20) is False
    assert len(ctx.messages) == 30
