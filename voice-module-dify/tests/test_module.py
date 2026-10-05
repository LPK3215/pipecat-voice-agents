"""Fast unit tests: no network, no model loading, no platform.

These cover the parts that silently break the voice experience -- the sentence splitter that
decides when the bot starts talking, the PCM contract the browser client depends on, the config
defaults, and the rule that a dead brain must be *sayable* rather than silent.
"""

import asyncio
import dataclasses

import pytest
from agent_client import BrainClient, BrainError
from brain import BrainProcessor
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame, TextFrame
from raw_pcm_serializer import RawPCMFrameSerializer
from settings import load_config


# ---------------------------------------------------------------- sentence splitting
def take(text: str, *, last: bool = False) -> list[str]:
    return BrainProcessor._take_sentences(text, last=last)


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
    # Control/text frames are not the client's business: the contract is "audio bytes only".
    assert asyncio.run(serializer.serialize(TextFrame("hi"))) is None


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
