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
from pipecat.observers.error_observer import ErrorObserver
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
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

from pipeline_logging import (
    ConversationLogger,
    install_client_logging,
    install_error_reporting,
    install_function_call_logging,
    log_boot_banner,
    resolve_log_paths,
    setup_logging,
)

# 日志路径只算一次，全程复用；出口在本模块最先挂上，确保捕获启动期事件
RUN_LOG, LATEST_LOG = resolve_log_paths("bot")
setup_logging(RUN_LOG, LATEST_LOG)

load_dotenv(override=True)

# ==========================================================================
# 配置（全部可通过环境变量覆盖）
#
# 默认值一律取自 settings.py：verify_stack.py 也读同一份，
# 因此「自检测到的配置」就是「bot 真正跑的配置」，不会两侧漂移。
# ==========================================================================
import memory  # noqa: E402 - 均需先 load_dotenv
from settings import (  # noqa: E402
    DEFAULT_OPENING_MESSAGE,
    DEFAULT_SYSTEM_INSTRUCTION,
    DEFAULT_VAD_STOP_SECS,
    VAD_STOP_SECS_OFFICIAL_DEFAULT,
    build_llm_extra,
    build_stt,
    build_tts,
    llm_config,
    stt_desc,
    stt_engine,
    thinking_disabled,
    tools_enabled,
    tts_desc,
    tts_engine,
)
from tools import build_tools  # noqa: E402 - 同理

# LLM 服务商（默认 modelscope，设 LLM_PROVIDER=suanli 切到共绩）——
# base_url / key / model 都从这里解析，切换只改环境变量，代码不动。
LLM_CFG = llm_config()

# 关闭「思考」模式：推理模型默认先推理再回答，实测首 token 会涨到 2~3 秒。
# 各家「关思考」的写法不同，具体注入什么由服务商的 thinking_body 决定
# （魔搭 enable_thinking / 商汤 thinking={"type":"disabled"} / 共绩暂不注入）。
DISABLE_THINKING = thinking_disabled()

# 是否开放工具调用（function calling）给 LLM。
# 前端无需改动：pipecat 会把调用过程与结果以 llm-function-call* 消息推给 Prebuilt 前端。
ENABLE_TOOLS = tools_enabled()
TOOLS = build_tools() if ENABLE_TOOLS else None

# STT / TTS 的引擎选择与构造逻辑在 settings.build_stt / build_tts ——
# 与 verify_stack.py 共用同一份，避免「测的」和「跑的」两侧漂移。

# VAD「说完」阈值。官方默认 0.2s 会把一句中文按逗号停顿切成两段
VAD_STOP_SECS = float(os.getenv("VAD_STOP_SECS", str(DEFAULT_VAD_STOP_SECS)))

SYSTEM_INSTRUCTION = os.getenv("SYSTEM_INSTRUCTION", DEFAULT_SYSTEM_INSTRUCTION)
OPENING_MESSAGE = os.getenv("OPENING_MESSAGE", DEFAULT_OPENING_MESSAGE)

VAD_STOP_SECS_DEFAULT = VAD_STOP_SECS_OFFICIAL_DEFAULT  # pipecat 官方推荐值，仅用于提示

# ---------- 本地持久化 ----------
# 框架只提供内存态的短期记忆（LLMContext），长期记忆只给了 mem0 适配器（云端要 key），
# 知识库与数据库完全没有 —— 这几块只能自己接。SQLite 零依赖，之后要换随时可换。
memory.init_db()
memory.seed_demo_business()  # 示例业务数据，接入真实采集后自动被覆盖
SESSION_ID = memory.new_session_id()


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

    recommended = LLM_CFG["recommended"]
    if recommended and LLM_CFG["model"] not in recommended:
        logger.warning(
            f"[BOOT] 模型 {LLM_CFG['model']} 不在实测推荐的 {len(recommended)} 个之内，"
            f"延迟可能不理想。推荐: {recommended}"
        )
    # ---------- 缺 key 必须 fail-fast ----------
    # 早期版本只打一行 ERROR 然后照常启动：浏览器能连上、握手也成功，
    # 但用户一开口必然没有回应，看起来像网络故障，极难排查。
    # 这里直接终止会话并给出可操作的指引。
    # 放在构造 STT/TTS 之前，避免缺 key 时仍去下载/加载本地模型。
    if not LLM_CFG["api_key"] and not os.getenv("ALLOW_MISSING_KEY"):
        raise RuntimeError(
            f"缺少 {LLM_CFG['api_key_env']}：LLM 调用必然失败。\n"
            f"  1) 打开 server/.env 填入 {LLM_CFG['api_key_env']}"
            f"（当前服务商 LLM_PROVIDER={LLM_CFG['provider']}，请确认与密钥匹配）\n"
            "  2) 重新启动：uv run bot.py\n"
            "  只想调试前端/传输层时，可设 ALLOW_MISSING_KEY=1 跳过本检查。"
        )

    log_boot_banner(
        {
            "会话 ID": session_id,
            "Pipecat 版本": _pipecat_version(),
            "LLM 服务商": LLM_CFG["provider"],
            "LLM 模型": LLM_CFG["model"],
            "关闭思考模式": DISABLE_THINKING,
            "工具调用": (
                ", ".join(t.name for t in TOOLS.standard_tools) if TOOLS else "未启用"
            ),
            "故障推送前端": "已开启（ErrorObserver → RTVI error）",
            "LLM Key": _mask(LLM_CFG["api_key"]),
            "STT（配置）": stt_desc(stt_engine()),
            "TTS（配置）": tts_desc(tts_engine()),
            "VAD 说完阈值": f"{VAD_STOP_SECS}s（官方推荐 {VAD_STOP_SECS_DEFAULT}s）",
            "系统提示词": SYSTEM_INSTRUCTION,
            "开场白": OPENING_MESSAGE,
            "本次日志": str(RUN_LOG),
        }
    )

    # ---------- Speech-to-Text / Text-to-Speech（本地，无 key） ----------
    # 放在横幅之后构造：构造会触发本地模型加载（首次还要联网下载，可能数十秒），
    # 不能让它挡住「本次配置」的可见性 —— 否则冷启动下载失败时连配置都看不到。
    # 实际生效的引擎单独记一行：配置了 sensevoice/kokoro 但依赖缺失时会降级。
    stt, stt_actual = build_stt()
    tts, tts_actual = build_tts()
    logger.info(f"[BOOT] STT 实际生效: {stt_actual}")
    logger.info(f"[BOOT] TTS 实际生效: {tts_actual}")

    # ---------- LLM（OpenAI 兼容：默认魔搭，可切共绩） ----------
    llm_kwargs: dict = {
        "model": LLM_CFG["model"],
        "system_instruction": SYSTEM_INSTRUCTION,
    }
    # pipecat 会把 OpenAILLMSettings.extra 里的键**直接作为 kwargs**
    # 传给 client.chat.completions.create(...)，因此非标准参数必须用
    # OpenAI SDK 的 extra_body 包一层，才能进入请求体 JSON。
    # 关思考的写法各服务商不同，交给 thinking_body。
    thinking_body = LLM_CFG["thinking_body"] if DISABLE_THINKING else None
    extra = build_llm_extra(thinking_body=thinking_body)
    if extra:
        llm_kwargs["extra"] = extra

    llm = OpenAILLMService(
        api_key=LLM_CFG["api_key"],
        base_url=LLM_CFG["base_url"],
        settings=OpenAILLMService.Settings(**llm_kwargs),
    )

    # ---------- 上下文与轮次管理 ----------
    # TOOLS 里的 FunctionSchema 自带 handler，LLM 服务会自动注册，
    # 因此不需要再手工调用 llm.register_function。
    context = LLMContext(tools=TOOLS) if TOOLS is not None else LLMContext()
    # 把长期记忆注入上下文 —— 模型看得到才算「记得」
    memory.load_memory_into_context(context)
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

    # 故障观察者必须先建（要作为 PipelineWorker 的构造参数传入）；
    # 而它把错误推给前端需要 worker.rtvi，所以事件处理在 worker 建好后再挂。
    error_observer = ErrorObserver()

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        # ConversationLogger：对话时间线 + 每轮分段延迟
        # MetricsLogObserver：各服务 TTFB / TTFAT / TTFA
        # ErrorObserver：在错误源头捕获，写 [ERROR] 日志并推送给前端
        # TurnRecorder：把每轮对话落库。必须是观察者，不能是管线处理器 ——
        #   用户/助手两侧的文本帧都会被各自的聚合器消费，不向下游转发
        #   （详见 memory.TurnRecorder 的说明）
        observers=[
            ConversationLogger(),
            MetricsLogObserver(),
            error_observer,
            # 工具调用按框架推荐方式记录（自带的 FunctionCallObserver）
            install_function_call_logging(),
            memory.TurnRecorder(SESSION_ID),
        ],
    )
    install_error_reporting(worker, error_observer)

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    # 前端（浏览器）侧事件日志：连接、断开、RTVI 消息、客户端信息
    install_client_logging(transport, worker.rtvi)

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        logger.info("[BOOT] 前端就绪，触发展示开场白")
        # 角色必须用 user，不能用 developer/system：
        #   1) 魔搭的 OpenAI 兼容接口不认 developer 角色，会 400
        #      「Unexpected message role」，且它会留在上下文里让后续每一轮都失败；
        #   2) 该接口还要求消息里**至少有一条 user**，否则 400
        #      「No user query found in messages.」，所以 system 角色也发不出去。
        context.add_message({"role": "user", "content": OPENING_MESSAGE})
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
