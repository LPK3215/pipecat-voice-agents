"""All configuration for the voice module, in one place.

Written from scratch for phase 3. The **values** (VAD 0.6, Whisper + explicit Chinese +
initial prompt, Piper for first-audio latency) are informed by the phase-1 handbook's
measurements -- that is knowledge, not code -- but nothing here imports or calls the
quickstart project.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent  # voice-module-dify/
load_dotenv(BASE / ".env")

# ---------------------------------------------------------------- helpers


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() not in ("0", "false", "no", "off")


def _num(name: str, default: float, cast=float):
    try:
        return cast(_env(name) or default)
    except (TypeError, ValueError):
        return cast(default)


# ---------------------------------------------------------------- config


@dataclass(frozen=True)
class BrainConfig:
    """The external agent platform: it owns the thinking."""

    base_url: str
    api_key: str
    console_url: str
    streaming: bool
    first_token_timeout: float
    # A stable per-installation user id; the platform uses it for its own bookkeeping.
    user: str

    @property
    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat-messages"

    def stop_url(self, task_id: str) -> str:
        return f"{self.chat_url}/{task_id}/stop"


@dataclass(frozen=True)
class VoiceConfig:
    """The local speech parts: local models, no keys."""

    stt_engine: str
    whisper_model: str
    stt_language: str
    #: Whisper's `initial_prompt`. Making it *your* vocabulary is the cheapest accuracy win
    #: available: measured here, adding business words fixed "保修期" (which a generic prompt
    #: heard as "保修气势") with no measurable latency cost -- while switching to a bigger model
    #: (`small`) was 2.7x slower and no more accurate.
    stt_prompt: str
    tts_engine: str
    piper_voice: str
    vad_stop_secs: float
    #: "Don't hand TTS a fragment": with a value set, short sentences keep accumulating before
    #: they are spoken. Smoother prosody (each synthesis call gets more to work with), at the
    #: cost of a later first word. 0 = speak on the very first sentence (the default: latency
    #: first). See `brain._take_sentences`.
    speak_min_chars: int


@dataclass(frozen=True)
class FillerConfig:
    """What the user hears while the platform is thinking (it is a network round-trip)."""

    text: str
    delay_secs: float


@dataclass(frozen=True)
class Config:
    brain: BrainConfig
    voice: VoiceConfig
    filler: FillerConfig
    stub_brain: bool
    logs_dir: Path


def load_config() -> Config:
    return Config(
        brain=BrainConfig(
            base_url=_env("BRAIN_BASE_URL", "http://localhost/v1"),
            api_key=_env("BRAIN_API_KEY"),
            console_url=_env("BRAIN_CONSOLE_URL", "http://localhost/console/api"),
            streaming=_flag("BRAIN_STREAMING", True),
            first_token_timeout=_num("BRAIN_FIRST_TOKEN_TIMEOUT", 15.0),
            user=_env("BRAIN_USER", "voice-module"),
        ),
        voice=VoiceConfig(
            stt_engine=_env("STT_ENGINE", "whisper"),
            whisper_model=_env("WHISPER_MODEL", "base"),
            stt_language=_env("STT_LANGUAGE", "zh"),
            stt_prompt=_env("STT_PROMPT", "以下是普通话的句子。"),
            tts_engine=_env("TTS_ENGINE", "piper"),
            piper_voice=_env("PIPER_VOICE_ID", "zh_CN-huayan-medium"),
            vad_stop_secs=_num("VAD_STOP_SECS", 0.6),
            speak_min_chars=int(_num("SPEAK_MIN_CHARS", 0)),
        ),
        filler=FillerConfig(
            text=_env("FILLER_TEXT", "嗯，我看一下。"),
            delay_secs=_num("FILLER_DELAY_SECS", 0.8),
        ),
        stub_brain=_flag("BRAIN_STUB", False),
        logs_dir=BASE / "logs",
    )


# ---------------------------------------------------------------- services
# Built here so the pipeline, the probes and any future entry point share exactly one
# source of truth -- a split between "what we test" and "what we run" produces latency
# numbers nobody can trust (phase 1 documented that lesson).


def build_stt(cfg: Config):
    """Speech -> text. Whisper locally; explicit Chinese + the configured prompt.

    Why the prompt: whisper `base` otherwise emits Traditional characters (reads wrong for a
    Mandarin assistant), and -- measured here -- adding **your own vocabulary** to it fixes the
    words that actually matter: "保修期" was heard as "保修气势" until the prompt listed it.
    Why explicit language: it does not change accuracy, it only saves ~250ms per utterance
    (phase-1 measurement).
    """
    from pipecat.services.whisper.stt import WhisperSTTService

    settings = WhisperSTTService.Settings(
        model=cfg.voice.whisper_model,
        language=cfg.voice.stt_language,
        initial_prompt=cfg.voice.stt_prompt,
    )
    return WhisperSTTService(settings=settings)


def build_tts(cfg: Config):
    """Text -> speech. Piper: first audio chunk in tens of milliseconds."""
    from pipecat.services.piper.tts import PiperTTSService

    return PiperTTSService(settings=PiperTTSService.Settings(voice=cfg.voice.piper_voice))


def build_vad(cfg: Config):
    """Decides "the user finished talking". 0.6s keeps a Chinese sentence whole."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams

    return SileroVADAnalyzer(params=VADParams(stop_secs=cfg.voice.vad_stop_secs))
