"""Fast unit tests: no network, no model loading, no platform.

These cover the parts that silently break the voice experience -- the sentence splitter that
decides when the bot starts talking, the PCM contract the browser client depends on, the config
defaults, and the rule that a dead brain must be *sayable* rather than silent.
"""

import asyncio
import dataclasses
import json

import httpx
import pytest
from agent_client import BrainClient, BrainError, normalize_events
from brain import BrainProcessor, filler_options
from pipecat.frames.frames import (
    InputAudioRawFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
    TextFrame,
)
from raw_pcm_serializer import RawPCMFrameSerializer
from settings import load_config


# ---------------------------------------------------------------- sentence splitting
def take(text: str, *, last: bool = False, min_chars: int = 0) -> list[str]:
    return BrainProcessor._take_sentences(text, last=last, min_chars=min_chars)


def test_splits_on_chinese_punctuation():
    """The bot should start talking on the first sentence, not on the full answer."""
    assert take("第一句。第二句还没说完") == ["第一句。"]
    assert take("问一句？然后再说") == ["问一句？"]


def test_flushes_the_tail_when_the_answer_ends():
    """Platforms often finish without punctuation; that tail must still be spoken."""
    assert take("没有标点的长尾巴", last=True) == ["没有标点的长尾巴"]


def test_caps_a_runaway_sentence():
    """A platform that never punctuates must not hold the audio forever."""
    pieces = take("啊" * 200, last=False)
    assert pieces and all(len(piece) <= 60 for piece in pieces)


def test_empty_input_produces_nothing():
    assert take("", last=True) == []
    assert take("   ", last=True) == []


def test_min_chars_accumulates_instead_of_speaking_fragments():
    """The "less choppy / later first word" knob (SPEAK_MIN_CHARS).

    Measured problem it addresses: handing TTS one short sentence at a time makes the speech
    sound clipped. 0 must stay the old behaviour, or turning the knob on would change nothing
    and turning it off would change everything.
    """
    assert take("短句。后面的内容还没到") == ["短句。"]              # default: speak immediately
    assert take("短句。后面的内容还没到", min_chars=8) == []        # wait, accumulate
    assert take("短句。后面的内容还没到", min_chars=8, last=True) == ["短句。后面的内容还没到"]


def test_filler_rotates_so_it_does_not_say_the_same_line_every_turn():
    assert filler_options("嗯，我看一下。|我看看啊。") == ["嗯，我看一下。", "我看看啊。"]
    assert filler_options("只有一句") == ["只有一句"]
    assert filler_options("") == []


# ---------------------------------------------------------------- PCM contract
def test_serializer_turns_client_bytes_into_microphone_audio():
    frame = asyncio.run(RawPCMFrameSerializer(sample_rate=16000).deserialize(b"\x01\x02" * 160))
    assert isinstance(frame, InputAudioRawFrame)
    assert frame.sample_rate == 16000
    assert frame.num_channels == 1


def test_serializer_sends_audio_out_and_nothing_else():
    serializer = RawPCMFrameSerializer()
    out = asyncio.run(
        serializer.serialize(
            OutputAudioRawFrame(audio=b"\x00\x01", sample_rate=16000, num_channels=1)
        )
    )
    assert out == b"\x00\x01"
    # Frames that are neither audio nor a message stay off the wire: the audio contract is
    # "binary frames are PCM", and adding chatter to it would break clients built on that.
    assert asyncio.run(serializer.serialize(TextFrame("hi"))) is None


# ---------------------------------------------------------------- the activity channel
def test_serializer_sends_events_as_json_text():
    """The page tells the two channels apart by frame type, so events must be text (str)."""
    serializer = RawPCMFrameSerializer()
    out = asyncio.run(
        serializer.serialize(OutputTransportMessageFrame(message={"kind": "tool", "text": "查"}))
    )
    assert isinstance(out, str)
    assert json.loads(out) == {"kind": "tool", "text": "查"}


def test_serializer_also_carries_urgent_events():
    """The module sends events as *urgent* frames (they bypass the TTS pause -- measured: as
    ordinary frames they arrived ~16s late, after the answer had been spoken). Both variants
    must reach the client, or turning the log "on time" would silently drop it instead."""
    serializer = RawPCMFrameSerializer()
    out = asyncio.run(
        serializer.serialize(
            OutputTransportMessageUrgentFrame(message={"kind": "state", "state": "speaking"})
        )
    )
    assert isinstance(out, str)
    assert json.loads(out) == {"kind": "state", "state": "speaking"}


def test_serializer_ignores_text_from_the_client():
    """No second control channel: audio in, nothing else."""
    assert asyncio.run(RawPCMFrameSerializer().deserialize('{"kind": "hi"}')) is None


def test_one_platform_event_can_carry_thought_tool_and_result():
    notes = normalize_events(
        {
            "event": "agent_thought",
            "thought": "要不要查一下",
            "tool": "search_knowledge",
            "tool_input": '{"query": "保修"}',
            "observation": "命中 2 条",
        }
    )
    assert [note["kind"] for note in notes] == ["thinking", "tool", "result"]
    assert notes[1]["detail"] == '{"query": "保修"}'


def test_node_events_carry_progress_and_elapsed():
    started = normalize_events({"event": "node_started", "data": {"title": "检索"}})[0]
    finished = normalize_events(
        {"event": "node_finished", "data": {"title": "检索", "elapsed_time": 0.31}}
    )[0]
    assert started["state"] == "started" and started["elapsed"] is None
    assert finished["state"] == "finished" and finished["elapsed"] == 0.31


@pytest.mark.parametrize(
    "event", [{"event": "ping"}, {"event": "message_end"}, {"event": "tts_message"}, {}]
)
def test_noise_is_dropped_instead_of_shown(event):
    """Keep-alives and unknown events must vanish, not become log lines or crash the stream."""
    assert normalize_events(event) == []


def test_client_emits_events_live_and_still_streams_the_answer():
    """The regression that matters: turning events on must not disturb the answer path."""
    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    body = "".join(
        [
            sse(
                {
                    "event": "agent_thought",
                    "thought": "先看看",
                    "conversation_id": "c1",
                    "task_id": "t1",
                }
            ),
            sse({"event": "agent_thought", "tool": "search_knowledge", "observation": "2 条"}),
            sse({"event": "message", "answer": "你"}),
            sse({"event": "message", "answer": "好"}),
            sse({"event": "ping"}),
            "data: [DONE]\n\n",
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    seen: list[dict] = []

    async def on_event(note: dict) -> None:
        seen.append(note)

    async def run() -> tuple[str, list[dict]]:
        cfg = dataclasses.replace(
            load_config().brain, base_url="http://brain.test/v1", api_key="app-test"
        )
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with http, BrainClient(cfg, client=http, on_event=on_event) as client:
            text = ""
            async for delta in client.stream("在吗"):
                text += delta
            return text, client.last_turn.events

    text, events = asyncio.run(run())
    assert text == "你好"  # the answer is untouched
    assert [note["kind"] for note in seen] == ["thinking", "tool", "result"]
    assert [note["kind"] for note in events] == ["thinking", "tool", "result"]  # kept for probes


def test_serializer_ignores_empty_payloads():
    assert asyncio.run(RawPCMFrameSerializer().deserialize(b"")) is None


# ---------------------------------------------------------------- configuration
def test_defaults_point_at_a_local_platform_and_the_stub_is_off(monkeypatch):
    monkeypatch.delenv("BRAIN_BASE_URL", raising=False)
    monkeypatch.delenv("BRAIN_STUB", raising=False)
    cfg = load_config()
    assert cfg.brain.base_url.endswith("/v1")
    assert cfg.stub_brain is False  # never silently fake the brain


@pytest.mark.parametrize("raw", ["1", "true", "True", "yes"])
def test_stub_flag_accepts_the_usual_truthy_spellings(monkeypatch, raw):
    monkeypatch.setenv("BRAIN_STUB", raw)
    assert load_config().stub_brain is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off"])
def test_stub_flag_stays_off_for_falsey_spellings(monkeypatch, raw):
    monkeypatch.setenv("BRAIN_STUB", raw)
    assert load_config().stub_brain is False


def test_stt_prompt_is_configurable_and_reaches_the_service(monkeypatch):
    """The cheapest accuracy knob: your own vocabulary in Whisper's initial prompt.

    Measured: listing the words this system hears fixed "保修期" (heard as "保修气势" with a
    generic prompt) at no measurable latency cost. It must be settable without touching code.
    """
    monkeypatch.setenv("STT_PROMPT", "以下是普通话的句子。常见词：保修期。")
    assert load_config().voice.stt_prompt.endswith("保修期。")
    monkeypatch.delenv("STT_PROMPT", raising=False)
    assert load_config().voice.stt_prompt == "以下是普通话的句子。"


def test_chat_and_stop_urls_are_derived_from_the_base():
    cfg = load_config()
    assert cfg.brain.chat_url.endswith("/chat-messages")
    assert cfg.brain.stop_url("abc").endswith("/chat-messages/abc/stop")


def test_a_bad_vad_value_falls_back_instead_of_crashing(monkeypatch):
    monkeypatch.setenv("VAD_STOP_SECS", "很久")
    assert load_config().voice.vad_stop_secs == 0.6


# ---------------------------------------------------------------- failure must be sayable
def test_unreachable_brain_raises_something_the_assistant_can_say():
    """A silent failure here looks exactly like "the bot never answers" -- so it must raise."""
    cfg = dataclasses.replace(
        load_config().brain,
        base_url="http://127.0.0.1:1/v1",  # nothing listens there
        api_key="app-test",
    )

    async def run() -> str:
        async with BrainClient(cfg) as client:
            try:
                async for _ in client.stream("在吗"):
                    pass
            except BrainError as exc:
                return exc.spoken
        raise AssertionError("expected BrainError")

    spoken = asyncio.run(run())
    assert (spoken and "错" in spoken) or "连不上" in spoken


def test_missing_api_key_is_reported_before_any_request():
    cfg = dataclasses.replace(load_config().brain, api_key="")
    with pytest.raises(BrainError) as excinfo:
        asyncio.run(_first_bite(cfg))
    assert excinfo.value.spoken  # still sayable
    assert "BRAIN_API_KEY" in str(excinfo.value)


async def _first_bite(brain_cfg) -> None:
    async with BrainClient(brain_cfg) as client:
        async for _ in client.stream("在吗"):
            return
