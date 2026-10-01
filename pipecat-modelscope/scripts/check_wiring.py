"""连通性/装配自检脚本（离线，不需要任何 API Key）。

逐项检查：
    A. 项目声明的依赖模块能否导入（含 test_e2e.py 用到的可选依赖）
    B. bot.py 声明的服务类能否实例化，Pipeline 能否按声明顺序装配
    C. test_e2e.py 用的本地 STT/TTS 能否实例化
    D. 运行环境与关键包版本
    E. 三个 API Key 环境变量的到位情况

用法：
    uv run scripts/check_wiring.py
"""

import os
import platform
import sys
import traceback

results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        detail = fn() or ""
        results.append((name, True, str(detail)))
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc().strip().splitlines()[-1]
        results.append((name, False, f"{type(exc).__name__}: {str(exc)[:150]} || {tb[:150]}"))


# --------------------------------------------------------------------------
# A. 模块导入
# --------------------------------------------------------------------------
IMPORTS = [
    # --- pyproject.toml 的 extras 对应模块 ---
    "pipecat.pipeline.pipeline",
    "pipecat.pipeline.worker",
    "pipecat.processors.aggregators.llm_context",
    "pipecat.processors.aggregators.llm_response_universal",
    "pipecat.audio.vad.silero",
    "pipecat.observers.loggers.metrics_log_observer",
    "pipecat.observers.user_bot_latency_observer",
    "pipecat.runner.run",
    "pipecat.runner.utils",
    "pipecat.services.deepgram.stt",
    "pipecat.services.cartesia.tts",
    "pipecat.services.openai.llm",
    "pipecat.transports.base_transport",
    "pipecat.workers.runner",
    # --- test_e2e.py 额外依赖 ---
    "pipecat.services.whisper.stt",
    "pipecat.services.piper.tts",
    "piper",
    "piper.download_voices",
    "faster_whisper",
    "soxr",
    "numpy",
    "dotenv",
    "loguru",
    "openai",
]

for mod in IMPORTS:
    check(f"import {mod}", lambda m=mod: __import__(m) and "ok")


# --------------------------------------------------------------------------
# 辅助：一个占位处理器（pipecat 1.12 无 PassThrough）
# --------------------------------------------------------------------------
def make_endpoint(name: str):
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    class Endpoint(FrameProcessor):
        def __init__(self):
            super().__init__(name=name)

        async def process_frame(self, frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    return Endpoint()


def describe(pipeline) -> str:
    procs = getattr(pipeline, "processors", None) or getattr(pipeline, "_processors", [])
    return " -> ".join(type(p).__name__ for p in procs)


# --------------------------------------------------------------------------
# B. bot.py 装配
# --------------------------------------------------------------------------
def build_bot_pipeline():
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.services.cartesia.tts import CartesiaTTSService
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService

    stt = DeepgramSTTService(api_key="fake-key")
    tts = CartesiaTTSService(
        api_key="fake-key",
        settings=CartesiaTTSService.Settings(voice="86e30c1d-714b-4074-a1f2-1cb6b552fb49"),
    )
    llm = OpenAILLMService(
        api_key="fake-key",
        base_url="https://api-inference.modelscope.cn/v1",
        settings=OpenAILLMService.Settings(model="Qwen/Qwen3.8-Flash-Next"),
    )
    context = LLMContext()
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )
    pipeline = Pipeline(
        [
            make_endpoint("input"),
            stt,
            user_agg,
            llm,
            tts,
            make_endpoint("output"),
            assistant_agg,
        ]
    )
    return describe(pipeline)


check("bot.py: 服务实例化 + Pipeline 装配", build_bot_pipeline)


def build_bot_pipeline_empty_key():
    """模拟真实情况：环境没有 key（os.getenv 返回 None）。"""
    from pipecat.services.cartesia.tts import CartesiaTTSService
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService

    DeepgramSTTService(api_key=None)
    CartesiaTTSService(api_key=None)
    OpenAILLMService(api_key=None, base_url="https://api-inference.modelscope.cn/v1")
    return "三种服务都能在无 key 情况下完成构造（错误要到连接时才暴露）"


check("bot.py: 无 Key 时服务构造行为", build_bot_pipeline_empty_key)


# --------------------------------------------------------------------------
# C. test_e2e.py 装配
# --------------------------------------------------------------------------
def build_e2e_pipeline():
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.services.piper.tts import PiperTTSService
    from pipecat.services.whisper.stt import Model as WhisperModel
    from pipecat.services.whisper.stt import WhisperSTTService

    stt = WhisperSTTService(model=WhisperModel("base"), device="cpu", compute_type="int8")
    tts = PiperTTSService(settings=PiperTTSService.Settings(voice="zh_CN-huayan-medium"))
    llm = OpenAILLMService(
        api_key="fake-key",
        base_url="https://api-inference.modelscope.cn/v1",
        settings=OpenAILLMService.Settings(model="Qwen/Qwen3.8-Flash-Next"),
    )
    LLMContextAggregatorPair(
        LLMContext(), user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )
    return f"WhisperSTT({WhisperModel.__name__}) + PiperTTS + OpenAILLM 均可实例化"


check("test_e2e.py: 本地 STT/TTS 服务实例化", build_e2e_pipeline)


# --------------------------------------------------------------------------
# D. 环境
# --------------------------------------------------------------------------
def env_info() -> str:
    import pipecat

    return (
        f"python={platform.python_version()} "
        f"pipecat={getattr(pipecat, '__version__', '?')} "
        f"platform={platform.system()}/{platform.machine()}"
    )


check("环境信息", env_info)


# --------------------------------------------------------------------------
# E. API Key 盘点
# --------------------------------------------------------------------------
def key_inventory() -> str:
    import pathlib

    env_file = pathlib.Path(__file__).resolve().parent.parent / ".env"
    rows = []
    for var, svc in (
        ("MODELSCOPE_API_KEY", "LLM"),
        ("DEEPGRAM_API_KEY", "STT"),
        ("CARTESIA_API_KEY", "TTS"),
    ):
        val = os.getenv(var)
        state = "SET" if val else "MISSING"
        rows.append(f"{var}({svc})={state}")
    present = "存在" if env_file.exists() else "不存在"
    return f"{' | '.join(rows)} || .env 文件: {present}"


check("API Key 盘点", key_inventory)


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
print("=" * 78)
print("Pipecat 项目连通性 / 装配自检")
print("=" * 78)
passed = sum(1 for _, ok, _ in results if ok)
for name, ok, detail in results:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    if detail and detail != "ok":
        print(f"       {detail}")
print("-" * 78)
print(f"结果: {passed}/{len(results)} 通过")
print("=" * 78)

sys.exit(0 if passed == len(results) else 1)
