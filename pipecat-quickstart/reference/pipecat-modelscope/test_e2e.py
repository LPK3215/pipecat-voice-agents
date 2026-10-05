"""端到端延迟实测（无头运行，不需要麦克风 / 浏览器 / 云端 STT-TTS key）。

链路：
    合成语音 WAV -> Whisper(本地 STT) -> 魔搭 ModelScope(LLM) -> Piper(本地 TTS) -> 统计

输出各阶段耗时：
    语音播放结束 -> VAD 判定说完
    语音播放结束 -> STT 出最终文本
    语音播放结束 -> LLM 首 token
    语音播放结束 -> TTS 首帧音频
    端到端 = 语音播放结束 -> 机器人开口

用法：
    export MODELSCOPE_API_KEY=ms-xxxx
    uv run test_e2e.py                       # 默认中文
    uv run test_e2e.py --lang en             # 英文
    uv run test_e2e.py --voice zh_CN-huayan-medium
日志同时写入 e2e-<时间戳>.log
"""

import argparse
import asyncio
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np
import soxr
from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    LLMTextFrame,
    StartFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.piper.tts import PiperTTSService
from pipecat.services.whisper.stt import Model as WhisperModel
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transcriptions.language import Language
from pipecat.workers.runner import WorkerRunner

load_dotenv(override=True)

# LLM 已固定为实测最快的模型，选型依据见 logs/ANALYSIS.md 与 README.md
MODELSCOPE_BASE_URL = "https://api-inference.modelscope.cn/v1"
MODELSCOPE_MODEL = "nex-agi/Nex-N2.5-mini"

CACHE_DIR = Path.home() / ".cache" / "pipecat" / "piper"
TARGET_SR = 16000


# --------------------------------------------------------------------------
# 时间线
# --------------------------------------------------------------------------
class Timeline:
    """记录关键事件的时间戳。"""

    def __init__(self) -> None:
        self.marks: dict[str, float] = {}
        self.audio_end: float | None = None
        self.transcript: str = ""
        self.reply: str = ""
        self.done = asyncio.Event()

    def mark(self, key: str) -> None:
        self.marks.setdefault(key, time.perf_counter())


class TimelineObserver(BaseObserver):
    """观察管线中流动的帧，记录首次出现的时间。"""

    def __init__(self, tl: Timeline, **kwargs):
        super().__init__(**kwargs)
        self._tl = tl

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame

        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self._tl.mark("vad_stop")
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._tl.mark("user_stop")
        elif isinstance(frame, TranscriptionFrame):
            self._tl.transcript = frame.text
            self._tl.mark("stt_text")
        elif isinstance(frame, (LLMTextFrame, TextFrame)):
            if frame.text and frame.text.strip():
                self._tl.mark("llm_first")
            self._tl.reply += getattr(frame, "text", "") or ""
        elif isinstance(frame, TTSAudioRawFrame):
            self._tl.mark("tts_audio")
            self._tl.mark("bot_speaking")
            self._tl.done.set()
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._tl.mark("bot_speaking")


# --------------------------------------------------------------------------
# 音频源 / 汇
# --------------------------------------------------------------------------
class WavSource(FrameProcessor):
    """把一段 PCM 音频按实时速度推入管线，末尾补静音让 VAD 判定说完。"""

    def __init__(self, pcm: bytes, tl: Timeline, sample_rate: int = TARGET_SR, chunk_ms: int = 20):
        super().__init__()
        self._pcm = pcm
        self._tl = tl
        self._sr = sample_rate
        self._chunk = int(sample_rate * chunk_ms / 1000) * 2  # 16-bit mono
        self._started = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame) and not self._started:
            self._started = True
            await self.push_frame(frame, direction)
            self.create_task(self._feed())
            return
        await self.push_frame(frame, direction)

    async def _feed(self) -> None:
        await asyncio.sleep(0.3)  # 等下游就绪
        chunk_secs = self._chunk / (self._sr * 2)

        for i in range(0, len(self._pcm), self._chunk):
            await self.push_frame(
                InputAudioRawFrame(
                    audio=self._pcm[i : i + self._chunk],
                    sample_rate=self._sr,
                    num_channels=1,
                )
            )
            await asyncio.sleep(chunk_secs)

        # 音频播放结束（用户说完的时刻）
        self._tl.audio_end = time.perf_counter()

        # 补 1.2s 静音，让 VAD 判定说话结束
        silence = b"\x00" * self._chunk
        for _ in range(int(1.2 / chunk_secs)):
            await self.push_frame(
                InputAudioRawFrame(audio=silence, sample_rate=self._sr, num_channels=1)
            )
            await asyncio.sleep(chunk_secs)


class Sink(FrameProcessor):
    """终点：消费音频帧，其余帧继续向下游传播。

    注意：必须放行 CancelFrame。否则 PipelineWorker 等不到它抵达管线末端，
    收尾会固定阻塞约 20s，并在测试脚本的 finally 中抛 TimeoutError，
    导致已经测到的结果全部被丢弃、报告打印不出来。
    """

    def __init__(self) -> None:
        super().__init__()
        self.audio_bytes = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            self.audio_bytes += len(frame.audio)
            return
        await self.push_frame(frame, direction)


# --------------------------------------------------------------------------
# 测试音频生成
# --------------------------------------------------------------------------
def ensure_voice(voice: str) -> Path:
    from piper.download_voices import download_voice

    onnx = CACHE_DIR / f"{voice}.onnx"
    if not onnx.exists():
        logger.info(f"下载 Piper 语音模型: {voice}")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        download_voice(voice, CACHE_DIR)
    return onnx


def synth(text: str, voice: str, out: Path) -> None:
    from piper import PiperVoice

    onnx = ensure_voice(voice)
    logger.info(f"合成测试语音 -> {out}")
    piper_voice = PiperVoice.load(str(onnx))
    with wave.open(str(out), "wb") as wf:
        piper_voice.synthesize_wav(text, wf)


def load_pcm_16k(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wf:
        sr, ch, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    data = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype(np.int16)

    if sr != TARGET_SR:
        res = soxr.resample(data.astype(np.float32), sr, TARGET_SR)
        data = np.clip(res, -32768, 32767).astype(np.int16)

    return data.tobytes()


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
async def run_benchmark(args) -> Timeline:
    tl = Timeline()

    pcm = load_pcm_16k(Path(args.wav))
    duration = len(pcm) / (TARGET_SR * 2)
    logger.info(f"测试音频: {args.wav} | 16kHz 单声道 | 时长 {duration:.2f}s")

    source = WavSource(pcm, tl)
    stt = WhisperSTTService(
        settings=WhisperSTTService.Settings(
            model=WhisperModel(args.whisper),
            language=Language.ZH if args.lang == "zh" else Language.EN,
        ),
        device="cpu",
        compute_type="int8",
    )
    tts = PiperTTSService(settings=PiperTTSService.Settings(voice=args.voice))
    llm = OpenAILLMService(
        api_key=os.getenv("MODELSCOPE_API_KEY"),
        base_url=MODELSCOPE_BASE_URL,
        settings=OpenAILLMService.Settings(
            model=MODELSCOPE_MODEL,
            system_instruction="你是一个语音助手，回答要口语化、简短。",
            temperature=0.7,
        ),
    )

    context = LLMContext()
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    sink = Sink()
    pipeline = Pipeline([source, stt, user_agg, llm, tts, assistant_agg, sink])

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[TimelineObserver(tl)],
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    run_task = asyncio.create_task(runner.run())

    try:
        await asyncio.wait_for(tl.done.wait(), timeout=args.timeout)
    except TimeoutError:
        logger.error(f"超时 {args.timeout}s，未收到 TTS 音频")
    finally:
        await runner.cancel()
        try:
            await asyncio.wait_for(run_task, timeout=30)
        except (TimeoutError, asyncio.CancelledError):
            logger.warning("管线收尾超时，已强制结束（已采集的结果仍会输出）")

    return tl


def report(tl: Timeline) -> None:
    base = tl.audio_end
    print()
    print("=" * 72)
    print("端到端延迟实测结果")
    print("=" * 72)
    if base is None:
        print("音频未播完，测试无效")
        return

    rows = [
        ("语音结束 -> VAD 判定说完", tl.marks.get("vad_stop")),
        ("语音结束 -> STT 最终文本", tl.marks.get("stt_text")),
        ("语音结束 -> LLM 首 token", tl.marks.get("llm_first")),
        ("语音结束 -> TTS 首帧音频", tl.marks.get("tts_audio")),
        ("语音结束 -> 机器人开口", tl.marks.get("bot_speaking")),
    ]
    for name, ts in rows:
        value = f"{(ts - base) * 1000:8.0f} ms" if ts else "     N/A"
        print(f"  {name:<28}{value}")

    print("-" * 72)
    print(f"  识别文本 : {tl.transcript!r}")
    print(f"  模型回复 : {tl.reply[:120]!r}")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipecat 端到端延迟实测")
    parser.add_argument("--lang", choices=["zh", "en"], default="zh")
    parser.add_argument("--voice", default=None)
    parser.add_argument("--whisper", default="base", help="tiny/base/small/medium")
    parser.add_argument("--wav", default=None, help="复用已有 WAV，不重新合成")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    if not os.getenv("MODELSCOPE_API_KEY"):
        raise SystemExit("请先设置 MODELSCOPE_API_KEY")

    log_file = Path(__file__).parent / f"e2e-{time.strftime('%Y%m%d-%H%M%S')}.log"
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.add(log_file, level="DEBUG")
    logger.info(f"日志文件: {log_file}")

    if args.voice is None:
        args.voice = "zh_CN-huayan-medium" if args.lang == "zh" else "en_US-lessac-medium"

    text = (
        "你好，请用一句话介绍一下你自己。"
        if args.lang == "zh"
        else "Hello, please introduce yourself in one sentence."
    )

    if args.wav is None:
        wav_path = Path(__file__).parent / f"test-input-{args.lang}.wav"
        if not wav_path.exists():
            synth(text, args.voice, wav_path)
        args.wav = str(wav_path)

    logger.info(f"STT=Whisper({args.whisper}) | LLM=ModelScope | TTS=Piper({args.voice})")
    tl = asyncio.run(run_benchmark(args))
    report(tl)


if __name__ == "__main__":
    main()
