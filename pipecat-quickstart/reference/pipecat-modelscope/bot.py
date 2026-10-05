"""最小可跑的 Pipecat 语音 Agent（连通性 / 及时性测试版）。

架构（级联式 Cascade）：
    transport.input() -> STT -> user_aggregator -> LLM -> TTS -> transport.output() -> assistant_aggregator

组件选型：
    - LLM  : 魔搭 ModelScope（OpenAI 兼容接口，base_url 指向 api-inference.modelscope.cn/v1）
    - STT  : Deepgram
    - TTS  : Cartesia
    - 传输 : SmallWebRTC（浏览器直连，无需第三方账号）

已打开：
    - enable_metrics / enable_usage_metrics -> 打印各服务 TTFB（首字节延迟）
    - MetricsLogObserver                    -> 控制台指标输出
    - UserBotLatencyObserver                -> "用户说完 -> 机器人开口" 端到端延迟

运行：
    cp .env.example .env      # 填入 MODELSCOPE / DEEPGRAM / CARTESIA 三个 key
    uv run bot.py
    浏览器打开 http://localhost:7860/client
"""

import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.observers.loggers.metrics_log_observer import MetricsLogObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

load_dotenv(override=True)

# ==========================================================================
# LLM 配置（已固定，请勿更换为其他模型）
#
# 选型依据：2026-10-01 对 ModelScope 可用模型逐一实测，见 logs/ANALYSIS.md
#   nex-agi/Nex-N2.5-mini   直连首 token 366~523 ms（平均 460 ms）  ← 最快最稳
#                           端到端 934 ms（thinking 仅 0.005 s）
#   Qwen/Qwen3.8-Flash-Next 直连首 token 1509~5972 ms              ← 原默认，端到端 4153 ms
#   deepseek-ai/DeepSeek-V4.1-Flash  1879 ms                       ← 稳但慢
#   ZhipuAI/GLM-4.7-Flash   6~20 s 且无输出                        ← 不可用
#
# 注意：该模型经探测基本不进入「思考」模式（thinking 字符数≈2），
#       因此首 token ≈ 首个答案 token，无额外思考等待。
# ==========================================================================
MODELSCOPE_BASE_URL = "https://api-inference.modelscope.cn/v1"
MODELSCOPE_MODEL = "nex-agi/Nex-N2.5-mini"

SYSTEM_INSTRUCTION = (
    "你是一个语音助手。你的回答会被朗读出来，所以请口语化、简短，"
    "不要使用 emoji、markdown、列表等无法朗读的格式。"
)


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    """为单个会话构建并运行语音管线。"""
    logger.info("Starting bot")
    logger.info(f"LLM = ModelScope | {MODELSCOPE_BASE_URL} | {MODELSCOPE_MODEL}")

    # ---------- Speech-to-Text ----------
    stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))

    # ---------- Text-to-Speech ----------
    tts = CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        settings=CartesiaTTSService.Settings(
            voice=os.getenv("CARTESIA_VOICE_ID", "86e30c1d-714b-4074-a1f2-1cb6b552fb49"),
        ),
    )

    # ---------- LLM：魔搭 ModelScope（OpenAI 兼容） ----------
    llm = OpenAILLMService(
        api_key=os.getenv("MODELSCOPE_API_KEY"),
        base_url=MODELSCOPE_BASE_URL,
        settings=OpenAILLMService.Settings(
            model=MODELSCOPE_MODEL,
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.7,
        ),
    )

    # ---------- 上下文（多轮记忆） ----------
    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    # ---------- 管线组装 ----------
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    # ---------- 延迟观测（及时性测试核心） ----------
    latency_observer = UserBotLatencyObserver()

    @latency_observer.event_handler("on_latency_measured")
    async def on_latency_measured(*args):
        logger.success(f"[LATENCY] 用户说完 -> 机器人开口: {float(args[-1]):.2f} s")

    @latency_observer.event_handler("on_first_bot_speech_latency")
    async def on_first_bot_speech_latency(*args):
        logger.success(f"[LATENCY] 连接成功 -> 首次开口: {float(args[-1]):.2f} s")

    @latency_observer.event_handler("on_latency_breakdown")
    async def on_latency_breakdown(*args):
        logger.info(f"[LATENCY] 分解: {args[-1]}")

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[MetricsLogObserver(), latency_observer],
    )

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        context.add_message({"role": "developer", "content": "先用一句话简短地自我介绍。"})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await runner.cancel()

    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Runner 入口。"""
    transport_params = {
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    }
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
