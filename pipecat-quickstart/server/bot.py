#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#
"""pipecat-quickstart - Pipecat Voice Agent

级联管线：Speech-to-Text → LLM → Text-to-Speech

由 Pipecat 官方 CLI 生成，仅按需做了少量改动（见 README.md「相对官方模板的改动」）。

使用到的服务：
- Whisper (Speech-to-Text)  本地，无需 key
- ModelScope (LLM)          需要一个 key
- Piper (Text-to-Speech)    本地，无需 key

运行：
    uv run bot.py

日志（一次运行即可看清前后端全过程）：
    logs/bot-<时间戳>.log    本次运行完整日志（DEBUG 级，含 pipecat 内部细节）
    logs/bot-latest.log      固定名，永远指向最近一次运行
"""

import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.observers.loggers.metrics_log_observer import MetricsLogObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.piper.tts import PiperTTSService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

from pipeline_logging import (
    ConversationLogger,
    install_client_logging,
    log_boot_banner,
    resolve_log_paths,
    setup_logging,
)

# 日志路径只算一次，全程复用；出口在本模块最先挂上，确保捕获启动期事件
RUN_LOG, LATEST_LOG = resolve_log_paths("bot")
setup_logging(RUN_LOG, LATEST_LOG)

load_dotenv(override=True)

# ==========================================================================
# 配置（全部可通过环境变量覆盖，默认值已按实测最优预设；详见 .env.example）
# ==========================================================================
MODELSCOPE_BASE_URL = os.getenv("MODELSCOPE_BASE_URL", "https://api-inference.modelscope.cn/v1")

# 可选模型：三个均已实测「关闭思考后」首 token < 1 秒，默认取最快的
AVAILABLE_MODELS = [
    "nex-agi/Nex-N2.5-mini",  # 710 ms  ← 默认
    "Qwen/Qwen3.8-Flash-Next",  # 787 ms
    "deepseek-ai/DeepSeek-V4.1-Flash",  # 817 ms
]
DEFAULT_MODEL = AVAILABLE_MODELS[0]
MODELSCOPE_MODEL = os.getenv("MODELSCOPE_MODEL") or DEFAULT_MODEL

# 关闭「思考」模式：Qwen / DeepSeek 默认先推理再回答，实测首 token 2.5s → 0.8s。
DISABLE_THINKING = os.getenv("LLM_DISABLE_THINKING", "1") not in ("0", "false", "False")

# 官方默认 Systran/faster-distil-whisper-medium.en 是【纯英文】模型，中文须用多语种
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
PIPER_VOICE_ID = os.getenv("PIPER_VOICE_ID", "zh_CN-huayan-medium")

# VAD「说完」阈值。官方默认 0.2s 会把一句中文按逗号停顿切成两段
VAD_STOP_SECS = float(os.getenv("VAD_STOP_SECS", "0.6"))

SYSTEM_INSTRUCTION = os.getenv(
    "SYSTEM_INSTRUCTION",
    "你是一个语音助手，正在进行语音对话。你的回答会被朗读出来，"
    "所以请口语化、简短，不要使用 emoji、markdown、列表等无法朗读的格式。",
)
OPENING_MESSAGE = os.getenv("OPENING_MESSAGE", "先用一句话简短地自我介绍。")

VAD_STOP_SECS_DEFAULT = 0.2  # pipecat 官方推荐值，仅用于提示


def _mask(value: str | None) -> str:
    if not value:
        return "未设置 ❌"
    return f"已设置 ✅ ({value[:6]}…{value[-4:]})"


def _pipecat_version() -> str:
    import pipecat

    return getattr(pipecat, "__version__", "?")


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    """Run the voice bot for this session."""
    session_id = getattr(runner_args, "session_id", None) or "unknown"

    # pipecat runner 启动时会 logger.remove() 清掉所有日志出口，
    # 因此会话建立后必须重新挂上，否则本会话的日志不会落盘。
    setup_logging(RUN_LOG, LATEST_LOG)
    logger.info(f"[BOOT] ───────── 新会话 {session_id} ─────────")

    log_boot_banner(
        {
            "会话 ID": session_id,
            "Pipecat 版本": _pipecat_version(),
            "LLM 模型": MODELSCOPE_MODEL,
            "关闭思考模式": DISABLE_THINKING,
            "LLM Key": _mask(os.getenv("MODELSCOPE_API_KEY")),
            "STT": f"Whisper({WHISPER_MODEL}) 本地·无需 key",
            "TTS": f"Piper({PIPER_VOICE_ID}) 本地·无需 key",
            "VAD 说完阈值": f"{VAD_STOP_SECS}s（官方推荐 {VAD_STOP_SECS_DEFAULT}s）",
            "系统提示词": SYSTEM_INSTRUCTION,
            "开场白": OPENING_MESSAGE,
            "本次日志": str(RUN_LOG),
        }
    )

    if MODELSCOPE_MODEL not in AVAILABLE_MODELS:
        logger.warning(
            f"[BOOT] 模型 {MODELSCOPE_MODEL} 不在实测推荐的 3 个之内，延迟可能不理想。"
            f"推荐: {AVAILABLE_MODELS}"
        )
    if not os.getenv("MODELSCOPE_API_KEY"):
        logger.error("[BOOT] 缺少 MODELSCOPE_API_KEY，LLM 调用必然失败！请填写 server/.env")

    # ---------- Speech-to-Text（本地，无 key） ----------
    stt = WhisperSTTService(
        settings=WhisperSTTService.Settings(model=WHISPER_MODEL),
        # 本机实测 Whisper「说完→最终文本」0.66~0.78s，p99 取 1.0s。
        # 显式填入可消除 "ttfs_p99_latency not set" 告警。
        ttfs_p99_latency=1.0,
    )

    # ---------- Text-to-Speech（本地，无 key） ----------
    tts = PiperTTSService(settings=PiperTTSService.Settings(voice=PIPER_VOICE_ID))

    # ---------- LLM（魔搭 ModelScope，OpenAI 兼容） ----------
    llm_kwargs: dict = {
        "model": MODELSCOPE_MODEL,
        "system_instruction": SYSTEM_INSTRUCTION,
    }
    if DISABLE_THINKING:
        # pipecat 会把 OpenAILLMSettings.extra 里的键**直接作为 kwargs**
        # 传给 client.chat.completions.create(...)，因此非标准参数必须用
        # OpenAI SDK 的 extra_body 包一层，才能进入请求体 JSON。
        llm_kwargs["extra"] = {"extra_body": {"enable_thinking": False}}

    llm = OpenAILLMService(
        api_key=os.getenv("MODELSCOPE_API_KEY"),
        base_url=MODELSCOPE_BASE_URL,
        settings=OpenAILLMService.Settings(**llm_kwargs),
    )

    # ---------- 上下文与轮次管理 ----------
    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS))
        ),
    )

    # ---------- 管线 ----------
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

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        # ConversationLogger：对话时间线 + 每轮分段延迟
        # MetricsLogObserver：各服务 TTFB / TTFAT / TTFA
        observers=[ConversationLogger(), MetricsLogObserver()],
    )

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    # 前端（浏览器）侧事件日志：连接、断开、RTVI 消息、客户端信息
    install_client_logging(transport, worker.rtvi)

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        logger.info("[BOOT] 前端就绪，触发展示开场白")
        context.add_message({"role": "developer", "content": OPENING_MESSAGE})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("[CLIENT] 会话结束，开始清理资源")
        await runner.cancel()

    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Main bot entry point."""
    transport_params = {
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    }
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    logger.info(f"[BOOT] 进程启动 | 本次日志: {RUN_LOG}")
    logger.info(f"[BOOT] 固定名日志: {LATEST_LOG}（永远指向最近一次运行）")

    from pipecat.runner.run import main

    main()
