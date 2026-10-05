"""Single source of truth for runtime defaults.

The only reason this module exists: the README promises that "what the self-check
reports is what actually runs". ``bot.py`` and ``verify_stack.py`` need the same
defaults; if each kept its own copy, a change on one side would silently invalidate
the self-check -- and that kind of drift does not raise an error, it just makes people
base decisions on wrong latency numbers.

Convention:
    Defaults live here, plus the construction logic for the two local services
    (``build_stt`` / ``build_tts``). The real values still come from environment
    variables (read individually in bot.py). bot.py and verify_stack.py share both the
    defaults and the construction logic, so "what is tested is what runs".
"""

from __future__ import annotations

import os

from loguru import logger

# ---------- LLM providers (OpenAI compatible, switchable) ----------
# All providers use the same OpenAILLMService; switching only changes
# base_url / key / model. Default is modelscope (existing behavior);
# set LLM_PROVIDER=sensenova or suanli to switch.
LLM_PROVIDERS: dict[str, dict] = {
    "modelscope": {
        "base_url": "https://api-inference.modelscope.cn/v1",
        "base_url_env": "MODELSCOPE_BASE_URL",
        "api_key_env": "MODELSCOPE_API_KEY",
        "model_env": "MODELSCOPE_MODEL",
        "default_model": "nex-agi/Nex-N2.5-mini",
        # All three measured under 1s to first token with thinking disabled; default is fastest.
        "recommended": (
            "nex-agi/Nex-N2.5-mini",  # 710 ms  <- default
            "Qwen/Qwen3.8-Flash-Next",  # 787 ms
            "deepseek-ai/DeepSeek-V4.1-Flash",  # 817 ms
        ),
        # Disabling thinking is spelled differently per provider: ModelScope uses enable_thinking.
        "thinking_body": {"enable_thinking": False},
    },
    "sensenova": {
        # SenseNova (OpenAI compatible). Key looks like sk-...
        "base_url": "https://token.sensenova.cn/v1",
        "base_url_env": "SENSENOVA_BASE_URL",
        "api_key_env": "SENSENOVA_API_KEY",
        "model_env": "SENSENOVA_MODEL",
        "default_model": "sensenova-6.8-flash-lite",
        "recommended": (),
        # Measured: enable_thinking / chat_template_kwargs have no effect; only
        # thinking={"type":"disabled"} actually disables it (otherwise the first token
        # budget is burned on reasoning).
        "thinking_body": {"thinking": {"type": "disabled"}},
    },
    "suanli": {
        # Suanli MaaS (OpenAI compatible). Only key + base_url needed; change model only.
        # Note this is api.suanli.cn, NOT the compute Open API at openapi.suanli.cn.
        "base_url": "https://api.suanli.cn/v1",
        "base_url_env": "SUANLI_BASE_URL",
        "api_key_env": "SUANLI_API_KEY",
        "model_env": "SUANLI_MODEL",
        # Suanli model IDs look like "vendor/model"; check the console/model plaza.
        "default_model": "qwen/qwen3.8-27b",
        "recommended": (),
        # Its thinking-disable parameter is unverified; inject nothing to avoid a 400.
        "thinking_body": None,
    },
}
DEFAULT_PROVIDER = "modelscope"

# Backward-compatible aliases (self-check scripts / docs may still reference these).
MODELSCOPE_BASE_URL_DEFAULT = LLM_PROVIDERS["modelscope"]["base_url"]
AVAILABLE_MODELS: tuple[str, ...] = LLM_PROVIDERS["modelscope"]["recommended"]
DEFAULT_MODEL = LLM_PROVIDERS[DEFAULT_PROVIDER]["default_model"]

# Disable "thinking" mode: Qwen / DeepSeek reason before answering; measured first
# token 2.5s -> 0.8s with it off.
DISABLE_THINKING_DEFAULT = "1"

# Whether to expose tools (function calling) to the LLM. No frontend changes needed.
ENABLE_TOOLS_DEFAULT = "1"

# These environment values always mean "off".
_OFF_SENTINELS = ("0", "false", "False")

# NOTE: prompts below are intentionally Chinese -- they shape the bot's spoken
# behavior and must stay in the conversation language.
DEFAULT_SYSTEM_INSTRUCTION = (
    "你是一个语音助手，正在进行语音对话。你的回答会被朗读出来，"
    "所以请口语化、简短，不要使用 emoji、markdown、列表等无法朗读的格式。"
)
DEFAULT_OPENING_MESSAGE = "先用一句话简短地自我介绍。"

# ---------- STT / TTS (both local, no key) ----------
# The official default Systran/faster-distil-whisper-medium.en is English-only;
# Chinese needs a multilingual model.
DEFAULT_WHISPER_MODEL = "base"
DEFAULT_PIPER_VOICE = "zh_CN-huayan-medium"

# Whisper base leans towards Traditional Chinese; a Mandarin prompt pulls it back to
# Simplified (a measured leftover issue).
WHISPER_INITIAL_PROMPT = "以下是普通话的句子。"

# Measured Whisper "end of speech -> final text" 0.66-0.78s; p99 set to 1.0s.
# Setting it explicitly silences the "ttfs_p99_latency not set" warning.
WHISPER_TTFS_P99 = 1.0

# ---------- STT engine ----------
# Both are **local, free, no key**, but Chinese accuracy differs a lot
# (asr_bench.py, 6-sentence set):
#   sensevoice: 10.2% CER, 158ms/sentence -- non-autoregressive, built for Chinese
#   whisper   : 23.8% CER, 607ms/sentence (base) -- general but weak on Chinese
# Choosing sensevoice improves accuracy AND latency; it is not a trade-off.
DEFAULT_STT_ENGINE = "sensevoice"

# Explicit Chinese: letting Whisper auto-detect is both slower and less accurate
# (measured 607ms -> 370ms).
DEFAULT_STT_LANGUAGE = "zh"

# SenseVoice measured 158ms per sentence; p99 set to 0.5s for headroom.
SENSEVOICE_TTFS_P99 = 0.5

# ---------- TTS engine ----------
# Both are local and free, but the **first audio chunk** latency differs a lot
# (same Chinese sentence):
#   piper : 76ms  -- mechanical voice, almost no added latency
#   kokoro: 709ms -- clearly more natural (8 Chinese voices), costs +630ms
# Voice assistants are very sensitive to "how long until it starts talking", so the
# default is piper; set TTS_ENGINE=kokoro for a nicer voice.
# This IS a real trade-off: unlike ASR, there is no "both fast and good" option.
DEFAULT_TTS_ENGINE = "piper"
DEFAULT_KOKORO_VOICE = "zf_xiaoxiao"

# ---------- VAD ----------
# The official default of 0.2s splits a Chinese sentence at its comma pauses, so the
# LLM only receives half of it.
#
# NOTE: this value interacts with the framework's turn detector. When VAD stop_secs is
# >= the STT p99 latency, the smart-turn strategy collapses its internal wait, and an
# "INCOMPLETE" verdict then falls back to LLMUserAggregatorParams.user_turn_stop_timeout
# (framework default: 5.0s) -- up to 5 seconds of dead air. That is why the synthetic
# verify_stack.py harness passes an explicit small timeout instead of inheriting it.
# See HANDBOOK.md section 9, item 11.
DEFAULT_VAD_STOP_SECS = 0.6
VAD_STOP_SECS_OFFICIAL_DEFAULT = 0.2  # official pipecat value, used for hints only

# ---------- Context summarization ----------
# The framework owns the implementation (LLMContextSummarizer, created inside the
# assistant aggregator); we only supply the thresholds. Compaction kicks in once more than
# this many messages are unsummarized, and the newest few are always kept verbatim.
DEFAULT_SUMMARY_MAX_MESSAGES = 20
DEFAULT_SUMMARY_MAX_TOKENS = 8000

# ---------- Fixed test sentence used by the self-check scripts ----------
VERIFY_USER_TEXT = "你好，请用一句话介绍一下你自己。"


def thinking_disabled(value: str | None = None) -> bool:
    """Whether to disable the model's "thinking" mode. Default on (thinking disabled)."""
    raw = value if value is not None else os.getenv("LLM_DISABLE_THINKING", DISABLE_THINKING_DEFAULT)
    return raw not in _OFF_SENTINELS


def tools_enabled(value: str | None = None) -> bool:
    """Whether to expose function calling to the LLM. Default on."""
    raw = value if value is not None else os.getenv("ENABLE_TOOLS", ENABLE_TOOLS_DEFAULT)
    return raw not in _OFF_SENTINELS


def stt_engine(value: str | None = None) -> str:
    """Speech-to-text engine: ``sensevoice`` (default, best for Chinese) or ``whisper``.

    Unrecognized values silently fall back to the default -- a typo in the engine name
    should not stop the service from starting.
    """
    raw = (
        value if value is not None else os.getenv("STT_ENGINE", DEFAULT_STT_ENGINE)
    ).strip().lower()
    return raw if raw in ("sensevoice", "whisper") else DEFAULT_STT_ENGINE


def tts_engine(value: str | None = None) -> str:
    """Text-to-speech engine: ``piper`` (default, low latency) or ``kokoro`` (nicer, +630ms)."""
    raw = (
        value if value is not None else os.getenv("TTS_ENGINE", DEFAULT_TTS_ENGINE)
    ).strip().lower()
    return raw if raw in ("piper", "kokoro") else DEFAULT_TTS_ENGINE


def llm_provider(name: str | None = None) -> str:
    """Resolve the current LLM provider name (``modelscope`` / ``sensenova`` / ``suanli``).

    Unknown values silently fall back to the default -- a typo in the provider name
    should not stop the service from starting.
    """
    raw = (name or os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER)).strip().lower()
    return raw if raw in LLM_PROVIDERS else DEFAULT_PROVIDER


def llm_config(provider: str | None = None) -> dict:
    """Resolve base_url / key / model for the current provider (shared by bot and scripts).

    All providers are OpenAI compatible, so the bot uses one ``OpenAILLMService`` and
    switching only changes the result of this function. Default is modelscope.
    """
    name = llm_provider(provider)
    spec = LLM_PROVIDERS[name]
    return {
        "provider": name,
        "base_url": os.getenv(spec["base_url_env"], spec["base_url"]),
        "base_url_env": spec["base_url_env"],
        "api_key": os.getenv(spec["api_key_env"]),
        "api_key_env": spec["api_key_env"],
        "model": os.getenv(spec["model_env"]) or spec["default_model"],
        "model_env": spec["model_env"],
        "recommended": spec["recommended"],
        "thinking_body": spec["thinking_body"],
    }


def build_llm_extra(thinking_body: dict | None = None) -> dict:
    """Build the ``extra`` passed to OpenAILLMService.Settings.

    pipecat passes keys of ``Settings.extra`` **directly as kwargs** to
    ``client.chat.completions.create(...)``, so non-standard parameters must be wrapped
    in the OpenAI SDK's ``extra_body`` or they are rejected as unknown keyword arguments.

    ``thinking_body``: the per-provider "disable thinking" payload, supplied by the
    caller (see ``LLM_PROVIDERS[..]["thinking_body"]``):
        modelscope  ``{"enable_thinking": False}``
        sensenova   ``{"thinking": {"type": "disabled"}}`` (the only form measured to work)
        suanli      ``None`` -- unverified, injected nothing to avoid a 400
    ``None`` means "inject nothing".

    ``LLM_TEMPERATURE``: sampling temperature. **If unset, the server default is used**
    (usually very high), which shows up as **different results every time** on the
    discrete "call the tool or not" decision: with the same question and the same tools,
    only 2 of 5 repeats called the correct tool. Set it to 0 for reproducibility
    (at the cost of blander, less varied answers).

    Returns ``{}`` when there is nothing to inject -- do not send an empty ``extra_body``.
    """
    body: dict = dict(thinking_body) if thinking_body else {}

    temperature = os.getenv("LLM_TEMPERATURE")
    if temperature not in (None, ""):
        body["temperature"] = float(temperature)

    return {"extra_body": body} if body else {}


def build_summarization_config():
    """Auto context-summarization config for the framework's ``LLMContextSummarizer``.

    pipecat creates the summarizer **inside the assistant aggregator** and wires its events
    itself; turning it on via ``LLMAssistantAggregatorParams`` together with this config is
    the whole wiring. The summary is produced by the pipeline's LLM service through the
    normal frame round-trip, so no separate client is needed.

    This replaced a hand-written observer (``summarize.py``). That version worked, but the
    framework ships the same thing with token- as well as message-based triggers, manual
    triggering via ``LLMSummarizeContextFrame``, interruption handling and result
    validation -- see HANDBOOK.md section 7.1: check the framework before writing your own.
    """
    from pipecat.utils.context.llm_context_summarization import (
        LLMAutoContextSummarizationConfig,
    )

    return LLMAutoContextSummarizationConfig(
        max_unsummarized_messages=DEFAULT_SUMMARY_MAX_MESSAGES,
        max_context_tokens=DEFAULT_SUMMARY_MAX_TOKENS,
    )


# ---------------------------------------------------------------------------
# Local service construction (shared by bot.py and verify_stack.py)
#
# Both return ``(service, description)``. The description states the engine that is
# **actually in effect**: configuring sensevoice / kokoro falls back to whisper / piper
# when the dependency is missing, and the description must reflect that, otherwise
# "the BOOT banner equals the real config" is a lie.
# ---------------------------------------------------------------------------


def stt_desc(engine: str) -> str:
    """Human-readable STT description; shared by service construction and the banner.

    ``engine`` is the engine name **actually in effect** (may be whisper after a
    fallback from sensevoice).
    """
    if engine == "sensevoice":
        return "SenseVoice(iic/SenseVoiceSmall) local, no key (best for Chinese)"
    model = os.getenv("WHISPER_MODEL", DEFAULT_WHISPER_MODEL)
    return f"Whisper({model}) local, no key"


def tts_desc(engine: str) -> str:
    """Human-readable TTS description; ``engine`` is the engine name actually in effect."""
    if engine == "kokoro":
        voice = os.getenv("KOKORO_VOICE_ID", DEFAULT_KOKORO_VOICE)
        return f"Kokoro({voice}) local, no key (nicer voice, +630ms)"
    voice = os.getenv("PIPER_VOICE_ID", DEFAULT_PIPER_VOICE)
    return f"Piper({voice}) local, no key (first chunk 76ms)"


def _resolve_language(name: str):
    from pipecat.transcriptions.language import Language

    try:
        return Language(name)
    except ValueError:
        return Language.ZH


def build_stt(
    engine: str | None = None,
    whisper_model: str | None = None,
    language: str | None = None,
):
    """Construct the local STT service.

    Both engines run locally with no key; SenseVoice is **both faster and more accurate**
    on Chinese (asr_bench.py: 10.2% CER / 158ms vs Whisper base 23.8% / 607ms), hence the
    default; Whisper remains the general fallback. If the funasr dependency is missing it
    falls back to Whisper -- a missing optional dependency should not stop the service,
    but the fallback must be reported in the log and in the returned description.
    """
    from pipecat.services.whisper.stt import WhisperSTTService

    eng = stt_engine(engine)
    lang = _resolve_language(language or os.getenv("STT_LANGUAGE", DEFAULT_STT_LANGUAGE))
    model = whisper_model or os.getenv("WHISPER_MODEL", DEFAULT_WHISPER_MODEL)

    if eng == "sensevoice":
        try:
            from pipecat.services.funasr.stt import FunASRSTTService
        except ImportError as exc:
            logger.warning(
                f"[BOOT] SenseVoice unavailable ({exc}); falling back to Whisper "
                "(Chinese accuracy drops). Enable it with: "
                "uv sync --extra sensevoice "
                "(or: uv pip install torch torchaudio --index-url "
                "https://download.pytorch.org/whl/cpu && uv pip install funasr)"
            )
        else:
            service = FunASRSTTService(
                settings=FunASRSTTService.Settings(
                    model="iic/SenseVoiceSmall",
                    language=lang,
                    use_itn=True,  # normalize "三点" to "3点"
                ),
                ttfs_p99_latency=SENSEVOICE_TTFS_P99,
            )
            return service, stt_desc("sensevoice")

    service = WhisperSTTService(
        settings=WhisperSTTService.Settings(
            model=model,
            # Explicit Chinese: auto-detection is slower and less accurate (607ms -> 370ms).
            language=lang,
            # Whisper base converts Chinese to Traditional (and mishears "介绍" as
            # "接收"); a Mandarin prompt pulls it back to Simplified.
            initial_prompt=WHISPER_INITIAL_PROMPT,
        ),
        # Measured Whisper "end of speech -> final text" about 1.0s; p99 set to 1.0s.
        # Setting it explicitly silences the "ttfs_p99_latency not set" warning.
        ttfs_p99_latency=WHISPER_TTFS_P99,
    )
    return service, stt_desc("whisper")


def build_tts(
    engine: str | None = None,
    piper_voice: str | None = None,
    kokoro_voice: str | None = None,
):
    """Construct the local TTS service.

    Unlike ASR there is **no option that is both fast and good**; this is a real
    trade-off: for the same Chinese sentence the first audio chunk takes about 76ms with
    Piper and 709ms with Kokoro (+630ms). Voice assistants are very sensitive to "how
    long until it starts", so Piper is the default; set kokoro for a more natural voice.
    """
    from pipecat.services.piper.tts import PiperTTSService

    eng = tts_engine(engine)

    if eng == "kokoro":
        try:
            from pipecat.services.kokoro.tts import KokoroTTSService
        except ImportError as exc:
            logger.warning(f"[BOOT] Kokoro unavailable ({exc}); falling back to Piper")
        else:
            voice = kokoro_voice or os.getenv("KOKORO_VOICE_ID", DEFAULT_KOKORO_VOICE)
            return (
                KokoroTTSService(
                    settings=KokoroTTSService.Settings(
                        voice=voice, language=_resolve_language(DEFAULT_STT_LANGUAGE)
                    )
                ),
                tts_desc("kokoro"),
            )

    voice = piper_voice or os.getenv("PIPER_VOICE_ID", DEFAULT_PIPER_VOICE)
    return (
        PiperTTSService(settings=PiperTTSService.Settings(voice=voice)),
        tts_desc("piper"),
    )
