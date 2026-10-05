"""Official-stack end-to-end self-check (no browser, no microphone, no cloud STT/TTS key).

It reads the **same configuration** from server/.env and feeds a Chinese test audio clip
through the pipeline:
    test audio -> local STT -> LLM -> local TTS -> stats
proving that all three stages plus the orchestration really work without opening a browser.

Usage (run from the server dir with its virtualenv):
    cd server
    uv run ../scripts/verify_stack.py                                  # use the .env config
    uv run ../scripts/verify_stack.py --model Qwen/Qwen3.8-Flash-Next   # temporarily try another model
    uv run ../scripts/verify_stack.py --stop-secs 0.2                   # reproduce "split into two"
    uv run ../scripts/verify_stack.py --whisper small

Log: server/logs/verify-<timestamp>.log
"""

import argparse
import asyncio
import os
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from dotenv import load_dotenv  # noqa: E402

# Scripts live in scripts/; the project root (server/, sample-data/, docs/) is one level up.
BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / "server" / ".env", override=True)

import numpy as np  # noqa: E402
from loguru import logger  # noqa: E402
from pipecat.audio.vad.silero import SileroVADAnalyzer  # noqa: E402
from pipecat.audio.vad.vad_analyzer import VADParams  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    Frame,
    InputAudioRawFrame,
    LLMTextFrame,
    StartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed  # noqa: E402
from pipecat.observers.loggers.metrics_log_observer import MetricsLogObserver  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.processors.aggregators.llm_response_universal import (  # noqa: E402
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402
from pipecat.services.openai.llm import OpenAILLMService  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

from pipeline_logging import ConversationLogger, setup_logging  # noqa: E402
from settings import (  # noqa: E402
    DEFAULT_PIPER_VOICE,
    DEFAULT_SYSTEM_INSTRUCTION,
    DEFAULT_VAD_STOP_SECS,
    VERIFY_USER_TEXT,
    build_llm_extra,
    build_stt,
    build_tts,
    llm_config,
    thinking_disabled,
)

TARGET_SR = 16000

# This harness feeds ONE fixed, complete utterance, so it must not wait for the smart-turn
# model to decide whether the user is done. Left at the framework default (5.0s), an
# "INCOMPLETE" verdict from the turn analyzer silently adds 5 seconds of dead air to every
# measured stage -- and these numbers are supposed to describe the *pipeline*, not the turn
# detector. See HANDBOOK.md section 9 for the underlying interaction (VAD stop_secs vs the
# STT p99 latency).
HARNESS_TURN_STOP_TIMEOUT = 0.5


class Timeline:
    def __init__(self) -> None:
        self.marks: dict[str, float] = {}
        self.audio_end: float | None = None
        self.transcript = ""
        self.transcripts: list[str] = []
        self.vad_events: list[float] = []
        self.reply = ""
        self.done = asyncio.Event()

    def mark(self, key: str) -> None:
        self.marks.setdefault(key, time.perf_counter())


class TimelineObserver(BaseObserver):
    """Same as ConversationLogger: observe only the first push, otherwise reply/transcripts
    accumulate once per hop."""

    def __init__(self, tl: Timeline, **kwargs):
        kwargs.setdefault("observe_every_push", False)
        super().__init__(**kwargs)
        self._tl = tl

    async def on_push_frame(self, data: FramePushed) -> None:
        f = data.frame
        if isinstance(f, VADUserStoppedSpeakingFrame):
            self._tl.mark("vad_stop")
            self._tl.vad_events.append(time.perf_counter())
        elif isinstance(f, UserStoppedSpeakingFrame):
            self._tl.mark("user_stop")
        elif isinstance(f, TranscriptionFrame):
            self._tl.transcript = f.text
            self._tl.transcripts.append(f.text)
            self._tl.mark("stt_text")
        elif isinstance(f, LLMTextFrame):
            # Only LLMTextFrame, not TextFrame: TTSService defaults to push_text_frames=True
            # and pushes the just-synthesized text downstream again as a TextFrame. Counting
            # both would add the same reply 2-3 times and make it look like the model is
            # repeating itself.
            if f.text and f.text.strip():
                self._tl.mark("llm_first")
            self._tl.reply += getattr(f, "text", "") or ""
        elif isinstance(f, TTSAudioRawFrame):
            self._tl.mark("tts_audio")
            self._tl.mark("bot_speaking")
            self._tl.done.set()


class WavSource(FrameProcessor):
    """Push PCM into the pipeline at real-time speed, then append silence so VAD detects end of speech."""

    def __init__(self, pcm: bytes, tl: Timeline, sr: int = TARGET_SR, chunk_ms: int = 20):
        super().__init__(name="WavSource")
        self._pcm, self._tl, self._sr = pcm, tl, sr
        self._chunk = int(sr * chunk_ms / 1000) * 2
        self._started = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame) and not self._started:
            self._started = True
            await self.push_frame(frame, direction)
            self.create_task(self._feed())
            return
        await self.push_frame(frame, direction)

    async def _feed(self):
        await asyncio.sleep(0.3)
        secs = self._chunk / (self._sr * 2)
        for i in range(0, len(self._pcm), self._chunk):
            await self.push_frame(
                InputAudioRawFrame(
                    audio=self._pcm[i : i + self._chunk], sample_rate=self._sr, num_channels=1
                )
            )
            await asyncio.sleep(secs)
        self._tl.audio_end = time.perf_counter()
        silence = b"\x00" * self._chunk
        for _ in range(int(1.2 / secs)):
            await self.push_frame(
                InputAudioRawFrame(audio=silence, sample_rate=self._sr, num_channels=1)
            )
            await asyncio.sleep(secs)


class Sink(FrameProcessor):
    """Terminal: consumes audio frames only; all other frames (incl. CancelFrame) must pass
    through, otherwise shutdown blocks."""

    def __init__(self) -> None:
        super().__init__(name="Sink")
        self.audio_bytes = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            self.audio_bytes += len(frame.audio)
            return
        await self.push_frame(frame, direction)


def load_pcm16k(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wf:
        sr, ch, n = wf.getframerate(), wf.getnchannels(), wf.getnframes()
        raw = wf.readframes(n)
    data = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype(np.int16)
    if sr != TARGET_SR:
        try:
            import soxr

            res = soxr.resample(data.astype(np.float32), sr, TARGET_SR)
        except ImportError:
            n2 = int(len(data) * TARGET_SR / sr)
            res = np.interp(np.linspace(0, len(data) - 1, n2), np.arange(len(data)), data)
        data = np.clip(res, -32768, 32767).astype(np.int16)
    return data.tobytes()


def make_wav(voice: str, path: Path) -> None:
    from piper import PiperVoice
    from piper.download_voices import download_voice

    cache = Path.home() / ".cache" / "pipecat" / "piper"
    onnx = cache / f"{voice}.onnx"
    if not onnx.exists():
        logger.info(f"[VERIFY] downloading Piper voice: {voice}")
        cache.mkdir(parents=True, exist_ok=True)
        download_voice(voice, cache)
    logger.info(f"[VERIFY] synthesizing Chinese test audio -> {path}")
    pv = PiperVoice.load(str(onnx))
    with wave.open(str(path), "wb") as wf:
        pv.synthesize_wav(VERIFY_USER_TEXT, wf)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="override the current provider's model name")
    ap.add_argument("--whisper", default=None, help="force the Whisper engine and override the model name")
    ap.add_argument("--stt-engine", default=None, help="override STT_ENGINE (sensevoice/whisper)")
    ap.add_argument("--voice", default=None, help="override PIPER_VOICE_ID")
    ap.add_argument("--stop-secs", type=float, default=None, help="override VAD_STOP_SECS")
    args = ap.parse_args()

    # Defaults all come from settings.py -- same source as server/bot.py, so "tested == running".
    llm_cfg = llm_config()
    model = args.model or llm_cfg["model"]
    base_url = llm_cfg["base_url"]
    voice = args.voice or os.getenv("PIPER_VOICE_ID") or DEFAULT_PIPER_VOICE
    # --whisper implies "force the whisper engine"; otherwise keep the .env engine.
    stt_engine_arg = args.stt_engine or ("whisper" if args.whisper else None)
    stop_secs = (
        args.stop_secs
        if args.stop_secs is not None
        else float(os.getenv("VAD_STOP_SECS", str(DEFAULT_VAD_STOP_SECS)))
    )
    # How to disable thinking differs per provider; that is what thinking_body is for.
    disable_thinking = thinking_disabled()
    system_instruction = os.getenv("SYSTEM_INSTRUCTION") or DEFAULT_SYSTEM_INSTRUCTION

    if not llm_cfg["api_key"]:
        print(f"missing {llm_cfg['api_key_env']} (it belongs in server/.env)")
        return 1

    # Same construction logic as bot.py, so this tests the engine the bot actually runs.
    stt, stt_desc_actual = build_stt(engine=stt_engine_arg, whisper_model=args.whisper)
    tts, tts_desc_actual = build_tts(piper_voice=args.voice)

    print("=" * 74)
    print("Official-stack end-to-end self-check (same config as server/bot.py)")
    print(f"  STT = {stt_desc_actual}")
    print(f"  LLM = {llm_cfg['provider']} | {model}")
    print(f"          disable_thinking={disable_thinking}")
    print(f"  TTS = {tts_desc_actual}")
    print(f"  VAD stop_secs = {stop_secs}")
    print("=" * 74)

    wav = BASE / "sample-data" / "verify-input-zh.wav"
    if not wav.exists():
        make_wav(voice, wav)

    tl = Timeline()
    pcm = load_pcm16k(wav)
    logger.info(f"[VERIFY] test audio: {wav} | duration {len(pcm) / (TARGET_SR * 2):.2f}s")

    source = WavSource(pcm, tl)

    llm_kwargs: dict = {"model": model, "system_instruction": system_instruction}
    # Non-standard parameters must be wrapped in extra_body (pipecat passes extra keys as kwargs).
    extra = build_llm_extra(
        thinking_body=(llm_cfg["thinking_body"] if disable_thinking else None)
    )
    if extra:
        llm_kwargs["extra"] = extra
    llm = OpenAILLMService(
        api_key=llm_cfg["api_key"],
        base_url=base_url,
        settings=OpenAILLMService.Settings(**llm_kwargs),
    )

    context = LLMContext()
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=stop_secs)),
            user_turn_stop_timeout=HARNESS_TURN_STOP_TIMEOUT,
        ),
    )

    sink = Sink()
    pipeline = Pipeline([source, stt, user_agg, llm, tts, assistant_agg, sink])
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[MetricsLogObserver(), TimelineObserver(tl), ConversationLogger()],
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    run_task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(tl.done.wait(), timeout=120)
    except TimeoutError:
        logger.error("[VERIFY] timed out after 120s, no TTS audio received")
    finally:
        await runner.cancel()
        try:
            await asyncio.wait_for(run_task, timeout=30)
        except (TimeoutError, asyncio.CancelledError):
            logger.warning("[VERIFY] pipeline shutdown timed out; forcing exit")

    base = tl.audio_end
    print()
    print("=" * 74)
    print("results")
    print("=" * 74)
    if base:
        for name, key in (
            ("speech end -> VAD end of turn", "vad_stop"),
            ("speech end -> STT final text", "stt_text"),
            ("speech end -> LLM first token", "llm_first"),
            ("speech end -> TTS first audio", "tts_audio"),
            ("speech end -> bot speaks", "bot_speaking"),
        ):
            ts = tl.marks.get(key)
            print(f"  {name:<28}{(ts - base) * 1000:8.0f} ms" if ts else f"  {name:<28}     N/A")
    print("-" * 74)
    print(f"  transcript : {tl.transcript!r}")
    print(f"  all segments : {tl.transcripts}")
    print(f"  model reply : {tl.reply[:100]!r}")
    print(f"  TTS audio   : {sink.audio_bytes} bytes")
    print("=" * 74)

    # Known trap (HANDBOOK.md section 9, item 11): when the turn analyzer returns INCOMPLETE,
    # the framework falls back to its 5s user_turn_stop_timeout and every stage looks ~5s
    # slower. That is not a hard failure -- network latency varies -- so it warns instead of
    # failing the verdict, but it must not stay invisible.
    gap = (tl.marks["bot_speaking"] - base) if base and tl.marks.get("bot_speaking") else None
    if gap and gap > 4.0:
        print(
            f"  [WARN] speech end -> bot speaks is {gap:.1f}s (> 4s); "
            "check the turn-stop-timeout trap (HANDBOOK.md section 9, item 11)"
        )

    ok = (
        bool(tl.transcript.strip())
        and tl.marks.get("tts_audio") is not None
        and sink.audio_bytes > 0
    )
    print("verdict:", "[OK] full path passed" if ok else "[ERR] some stage did not pass")
    return 0 if ok else 1


if __name__ == "__main__":
    _, VERIFY_LOG = setup_logging(prefix="verify")
    logger.info(f"[VERIFY] log: {VERIFY_LOG}")
    sys.exit(asyncio.run(main()))
