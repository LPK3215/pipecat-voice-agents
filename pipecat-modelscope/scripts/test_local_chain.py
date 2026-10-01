"""本地链路验证（不需要任何 API Key）。

目的：把「缺少 Key」和「链路本身损坏」两件事分开。
    Part 1  用假 Key 走 ModelScope，确认失败原因只有鉴权（401），而非协议/网络问题
    Part 2  把 test_e2e.py 的 LLM 换成内置 Stub，跑通
            WAV -> Whisper(本地 STT) -> Stub(替代 LLM) -> Piper(本地 TTS) -> Sink
            证明音频源、VAD、STT、编排、TTS、观测器全部可用

    --sink legacy : 复用 test_e2e.py 的 Sink（只放行 StartFrame/EndFrame）
    --sink all    : 放行所有帧（包括 CancelFrame），用于 A/B 定位收尾卡顿

用法：
    uv run scripts/test_local_chain.py
    uv run scripts/test_local_chain.py --sink legacy --log logs/07-local-chain-legacy-sink.log
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

import openai
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipecat.frames.frames import (  # noqa: E402
    EndFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    StartFrame,
    TTSAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402
from pipecat.services.piper.tts import PiperTTSService  # noqa: E402
from pipecat.services.whisper.stt import Model as WhisperModel  # noqa: E402
from pipecat.services.whisper.stt import WhisperSTTService  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

import test_e2e as te  # noqa: E402  复用音频源/STT/TTS/观测器/报告

BASE_URL = "https://api-inference.modelscope.cn/v1"
MODEL = "Qwen/Qwen3.8-Flash-Next"
REPLY = "你好，我是一个语音助手，很高兴为你服务。"


# --------------------------------------------------------------------------
# Part 1：确认 ModelScope 的失败原因仅是鉴权
# --------------------------------------------------------------------------
def check_llm_auth() -> bool:
    print("=" * 72)
    print("Part 1  ModelScope 鉴权探测（使用假 Key）")
    print("=" * 72)

    try:
        client = openai.OpenAI(base_url=BASE_URL, api_key="ms-fake-key-for-test")
        start = time.perf_counter()
        ids = [m.id for m in client.models.list().data]
        cost = (time.perf_counter() - start) * 1000
        print(f"  [OK]   /models 免鉴权可达 | 模型 {len(ids)} 个 | 耗时 {cost:.0f} ms")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] /models 不可达: {exc}")
        return False

    try:
        client.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": "hi"}], max_tokens=8
        )
        print("  [WARN] 假 Key 竟然通过了鉴权，与预期不符")
        return False
    except openai.AuthenticationError as exc:
        print(f"  [OK]   chat/completions 假 Key -> 401 鉴权失败（符合预期）")
        print(f"         status={exc.status_code} | {str(exc)[:100]}")
        print("  => 结论：网络与协议正常，唯一缺口是缺少有效 MODELSCOPE_API_KEY")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] 预期 401，实际为 {type(exc).__name__}: {str(exc)[:150]}")
        return False


# --------------------------------------------------------------------------
# Part 2：本地链路（Stub 替代 LLM）
# --------------------------------------------------------------------------
class StubLLM(FrameProcessor):
    """替代 LLM：收到上下文后，按流式节奏吐出一段固定回复。"""

    def __init__(self, reply: str, chunk: int = 6, delay: float = 0.05):
        super().__init__(name="StubLLM")
        self._reply = reply
        self._chunk = chunk
        self._delay = delay

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            self.create_task(self._respond())
            return
        await self.push_frame(frame, direction)

    async def _respond(self):
        await self.push_frame(LLMFullResponseStartFrame())
        for i in range(0, len(self._reply), self._chunk):
            await self.push_frame(LLMTextFrame(text=self._reply[i : i + self._chunk]))
            await asyncio.sleep(self._delay)
        await self.push_frame(LLMFullResponseEndFrame())


class SinkAll(FrameProcessor):
    """终点：消费音频帧，其余帧（含 CancelFrame）继续放行。"""

    def __init__(self) -> None:
        super().__init__(name="SinkAll")
        self.audio_bytes = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            self.audio_bytes += len(frame.audio)
        if isinstance(frame, (StartFrame, EndFrame)) or not isinstance(
            frame, TTSAudioRawFrame
        ):
            await self.push_frame(frame, direction)


def make_sink(kind: str):
    if kind == "legacy":
        return te.Sink()
    return SinkAll()


async def run_local_chain(
    voice: str, whisper: str, wav: Path, timeout: float, sink_kind: str
) -> tuple[te.Timeline, float]:
    tl = te.Timeline()

    pcm = te.load_pcm_16k(wav)
    duration = len(pcm) / (te.TARGET_SR * 2)
    logger.info(f"测试音频: {wav} | 16kHz 单声道 | 时长 {duration:.2f}s")

    source = te.WavSource(pcm, tl)
    stt = WhisperSTTService(model=WhisperModel(whisper), device="cpu", compute_type="int8")
    tts = PiperTTSService(settings=PiperTTSService.Settings(voice=voice))
    stub = StubLLM(REPLY)

    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )

    user_agg, assistant_agg = LLMContextAggregatorPair(
        LLMContext(),
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    sink = make_sink(sink_kind)
    pipeline = Pipeline([source, stt, user_agg, stub, tts, assistant_agg, sink])

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[te.TimelineObserver(tl)],
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    run_task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(tl.done.wait(), timeout=timeout)
    except TimeoutError:
        logger.error(f"超时 {timeout}s，未收到 TTS 音频")
    finally:
        t_cancel = time.perf_counter()
        await runner.cancel()
        try:
            await asyncio.wait_for(run_task, timeout=40)
        except (TimeoutError, asyncio.CancelledError) as exc:
            logger.warning(f"管线未在 40s 内收尾: {type(exc).__name__}")
        teardown = time.perf_counter() - t_cancel
        logger.info(f"收尾耗时(cancel -> 管线结束): {teardown:.2f}s")

    tl.audio_bytes = sink.audio_bytes
    return tl, teardown


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipecat 本地链路验证")
    parser.add_argument("--sink", choices=["all", "legacy"], default="all")
    parser.add_argument("--voice", default="zh_CN-huayan-medium")
    parser.add_argument("--whisper", default="base")
    parser.add_argument("--log", default="logs/06-local-chain.log")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    log_file = Path(__file__).resolve().parent.parent / args.log
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.add(log_file, level="DEBUG")
    logger.info(f"日志文件: {log_file}")

    ok_auth = check_llm_auth()

    print()
    print("=" * 72)
    print(f"Part 2  本地链路 WAV -> Whisper -> Stub -> Piper -> Sink({args.sink})")
    print("=" * 72)

    wav = Path(__file__).resolve().parent.parent / "test-input-zh.wav"
    if not wav.exists():
        te.synth("你好，请用一句话介绍一下你自己。", args.voice, wav)

    logger.info(f"STT=Whisper({args.whisper}) | LLM=Stub(替代) | TTS=Piper({args.voice})")
    tl, teardown = asyncio.run(
        run_local_chain(args.voice, args.whisper, wav, args.timeout, args.sink)
    )
    te.report(tl)

    reached_tts = tl.marks.get("tts_audio") is not None
    got_transcript = bool(tl.transcript.strip())
    print("链路判定:")
    print(f"  Whisper 转写出文本      : {'是' if got_transcript else '否'}")
    print(f"  Stub 回复到达 TTS       : {'是' if reached_tts else '否'}")
    print(f"  TTS 产出音频字节        : {getattr(tl, 'audio_bytes', 0)}")
    print(f"  收尾耗时                : {teardown:.2f} s")
    print("=" * 72)

    stats = {
        "auth_ok": ok_auth,
        "transcript": got_transcript,
        "tts_ok": reached_tts,
        "teardown": teardown,
        "audio_bytes": getattr(tl, "audio_bytes", 0),
    }
    print(f"STATS {stats}")
    return 0 if (ok_auth and reached_tts) else 1


if __name__ == "__main__":
    sys.exit(main())
