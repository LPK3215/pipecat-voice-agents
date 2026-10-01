"""官方栈端到端自检（不需要浏览器、不需要麦克风、不需要云 STT/TTS key）。

它读取 server/.env 的**同一套配置**，把一段中文测试音频喂进管线：
    测试音频 → Whisper(本地 STT) → ModelScope(LLM) → Piper(本地 TTS) → 统计
用于在不打开浏览器的前提下，证明三个环节 + 整条编排都真的工作。

用法（在 server 目录下用它的虚拟环境运行）：
    cd server
    uv run ../verify_stack.py                                  # 用 .env 里的配置
    uv run ../verify_stack.py --model Qwen/Qwen3.8-Flash-Next   # 临时换模型对比
    uv run ../verify_stack.py --stop-secs 0.2                   # 复现"被切成两段"
    uv run ../verify_stack.py --whisper small

日志：server/logs/verify-<时间戳>.log
"""

import argparse
import asyncio
import os
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "server"))

from dotenv import load_dotenv  # noqa: E402

BASE = Path(__file__).resolve().parent
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
from pipecat.services.piper.tts import PiperTTSService  # noqa: E402
from pipecat.services.whisper.stt import WhisperSTTService  # noqa: E402
from pipecat.transcriptions.language import Language  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

from pipeline_logging import ConversationLogger, setup_logging  # noqa: E402
from settings import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_PIPER_VOICE,
    DEFAULT_SYSTEM_INSTRUCTION,
    DEFAULT_VAD_STOP_SECS,
    DEFAULT_WHISPER_MODEL,
    MODELSCOPE_BASE_URL_DEFAULT,
    VERIFY_USER_TEXT,
    WHISPER_INITIAL_PROMPT,
    WHISPER_TTFS_P99,
    build_llm_extra,
    thinking_disabled,
)

TARGET_SR = 16000


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
    """与 ConversationLogger 同理：只观察首跳，否则 reply / transcripts 会按跳数累加。"""

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
            # 只认 LLMTextFrame，不要把 TextFrame 也算进来：
            # TTSService 默认 push_text_frames=True，会把刚合成的文本再往下推一次
            # TextFrame。若两类都收，同一句回复会被累加 2~3 遍，
            # 报告里的「模型回复」看起来像模型在复读。
            if f.text and f.text.strip():
                self._tl.mark("llm_first")
            self._tl.reply += getattr(f, "text", "") or ""
        elif isinstance(f, TTSAudioRawFrame):
            self._tl.mark("tts_audio")
            self._tl.mark("bot_speaking")
            self._tl.done.set()


class WavSource(FrameProcessor):
    """按实时速度把 PCM 推进管线，末尾补静音让 VAD 判定说完。"""

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
    """终点：只消费音频帧；其余帧（含 CancelFrame）必须放行，否则收尾会阻塞。"""

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
        logger.info(f"[VERIFY] 下载 Piper 音色: {voice}")
        cache.mkdir(parents=True, exist_ok=True)
        download_voice(voice, cache)
    logger.info(f"[VERIFY] 合成中文测试音频 -> {path}")
    pv = PiperVoice.load(str(onnx))
    with wave.open(str(path), "wb") as wf:
        pv.synthesize_wav(VERIFY_USER_TEXT, wf)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="覆盖 MODELSCOPE_MODEL")
    ap.add_argument("--whisper", default=None, help="覆盖 WHISPER_MODEL")
    ap.add_argument("--voice", default=None, help="覆盖 PIPER_VOICE_ID")
    ap.add_argument("--stop-secs", type=float, default=None, help="覆盖 VAD_STOP_SECS")
    args = ap.parse_args()

    # 默认值一律取自 settings.py —— 与 server/bot.py 同源，保证「测的就是跑的」
    model = args.model or os.getenv("MODELSCOPE_MODEL") or DEFAULT_MODEL
    base_url = os.getenv("MODELSCOPE_BASE_URL", MODELSCOPE_BASE_URL_DEFAULT)
    whisper_model = args.whisper or os.getenv("WHISPER_MODEL") or DEFAULT_WHISPER_MODEL
    voice = args.voice or os.getenv("PIPER_VOICE_ID") or DEFAULT_PIPER_VOICE
    stop_secs = (
        args.stop_secs
        if args.stop_secs is not None
        else float(os.getenv("VAD_STOP_SECS", str(DEFAULT_VAD_STOP_SECS)))
    )
    disable_thinking = thinking_disabled()
    system_instruction = os.getenv("SYSTEM_INSTRUCTION") or DEFAULT_SYSTEM_INSTRUCTION

    if not os.getenv("MODELSCOPE_API_KEY"):
        print("缺少 MODELSCOPE_API_KEY（应写在 server/.env）")
        return 1

    print("=" * 74)
    print("官方栈端到端自检（与 server/bot.py 同配置）")
    print(f"  STT = Whisper({whisper_model})            纯本地，无 key")
    print(f"  LLM = {model}")
    print(f"          关闭思考={disable_thinking}")
    print(f"  TTS = Piper({voice})   纯本地，无 key")
    print(f"  VAD stop_secs = {stop_secs}")
    print("=" * 74)

    wav = BASE / "verify-input-zh.wav"
    if not wav.exists():
        make_wav(voice, wav)

    tl = Timeline()
    pcm = load_pcm16k(wav)
    logger.info(f"[VERIFY] 测试音频: {wav} | 时长 {len(pcm) / (TARGET_SR * 2):.2f}s")

    source = WavSource(pcm, tl)
    stt = WhisperSTTService(
        settings=WhisperSTTService.Settings(
            model=whisper_model,
            language=Language.ZH,
            initial_prompt=WHISPER_INITIAL_PROMPT,
        ),
        ttfs_p99_latency=WHISPER_TTFS_P99,
    )
    tts = PiperTTSService(settings=PiperTTSService.Settings(voice=voice))

    llm_kwargs: dict = {"model": model, "system_instruction": system_instruction}
    if disable_thinking:
        # 非标准参数必须用 extra_body 包一层（pipecat 会把 extra 的键直接当 kwargs 传）
        llm_kwargs["extra"] = build_llm_extra()
    llm = OpenAILLMService(
        api_key=os.getenv("MODELSCOPE_API_KEY"),
        base_url=base_url,
        settings=OpenAILLMService.Settings(**llm_kwargs),
    )

    context = LLMContext()
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=stop_secs))
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
        logger.error("[VERIFY] 超时 120s，未收到 TTS 音频")
    finally:
        await runner.cancel()
        try:
            await asyncio.wait_for(run_task, timeout=30)
        except (TimeoutError, asyncio.CancelledError):
            logger.warning("[VERIFY] 管线收尾超时，已强制结束")

    base = tl.audio_end
    print()
    print("=" * 74)
    print("结果")
    print("=" * 74)
    if base:
        for name, key in (
            ("语音结束 -> VAD 判定说完", "vad_stop"),
            ("语音结束 -> STT 最终文本", "stt_text"),
            ("语音结束 -> LLM 首 token", "llm_first"),
            ("语音结束 -> TTS 首帧音频", "tts_audio"),
            ("语音结束 -> 机器人开口", "bot_speaking"),
        ):
            ts = tl.marks.get(key)
            print(f"  {name:<28}{(ts - base) * 1000:8.0f} ms" if ts else f"  {name:<28}     N/A")
    print("-" * 74)
    print(f"  识别文本 : {tl.transcript!r}")
    print(f"  全部分段 : {tl.transcripts}")
    print(f"  模型回复 : {tl.reply[:100]!r}")
    print(f"  TTS 音频 : {sink.audio_bytes} 字节")
    print("=" * 74)

    ok = (
        bool(tl.transcript.strip())
        and tl.marks.get("tts_audio") is not None
        and sink.audio_bytes > 0
    )
    print("判定:", "✅ 全链路通过" if ok else "❌ 有环节未通过")
    return 0 if ok else 1


if __name__ == "__main__":
    _, VERIFY_LOG = setup_logging(prefix="verify")
    logger.info(f"[VERIFY] 日志: {VERIFY_LOG}")
    sys.exit(asyncio.run(main()))
