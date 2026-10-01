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
from pipecat.transcriptions.language import Language
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
    install_error_reporting,
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
from settings import (  # noqa: E402
    AVAILABLE_MODELS,
    DEFAULT_KOKORO_VOICE,
    DEFAULT_MODEL,
    DEFAULT_OPENING_MESSAGE,
    DEFAULT_PIPER_VOICE,
    DEFAULT_STT_LANGUAGE,
    DEFAULT_SYSTEM_INSTRUCTION,
    DEFAULT_VAD_STOP_SECS,
    DEFAULT_WHISPER_MODEL,
    MODELSCOPE_BASE_URL_DEFAULT,
    SENSEVOICE_TTFS_P99,
    VAD_STOP_SECS_OFFICIAL_DEFAULT,
    WHISPER_INITIAL_PROMPT,
    WHISPER_TTFS_P99,
    build_llm_extra,
    stt_engine,
    thinking_disabled,
    tools_enabled,
    tts_engine,
)

from tools import build_tools  # noqa: E402 - 与 settings 同理，需先 load_dotenv

import memory  # noqa: E402 - 同上

MODELSCOPE_BASE_URL = os.getenv("MODELSCOPE_BASE_URL", MODELSCOPE_BASE_URL_DEFAULT)

# 可选模型：三个均已实测「关闭思考后」首 token < 1 秒，默认取最快的
MODELSCOPE_MODEL = os.getenv("MODELSCOPE_MODEL") or DEFAULT_MODEL

# 关闭「思考」模式：Qwen / DeepSeek 默认先推理再回答，实测首 token 2.5s → 0.8s。
DISABLE_THINKING = thinking_disabled()

# 是否开放工具调用（function calling）给 LLM。
# 前端无需改动：pipecat 会把调用过程与结果以 llm-function-call* 消息推给 Prebuilt 前端。
ENABLE_TOOLS = tools_enabled()
TOOLS = build_tools() if ENABLE_TOOLS else None

# 语音识别引擎。两者都本地免费，但中文表现差距很大：
# sensevoice 字错率 10.2% / 158ms，whisper(base) 23.8% / 607ms（asr_bench.py 实测）
STT_ENGINE = stt_engine()
STT_LANGUAGE = os.getenv("STT_LANGUAGE", DEFAULT_STT_LANGUAGE)

# 语音合成引擎。音色与延迟是取舍关系，故默认取延迟更低的 Piper
TTS_ENGINE = tts_engine()

# 官方默认 Systran/faster-distil-whisper-medium.en 是【纯英文】模型，中文须用多语种
WHISPER_MODEL = os.getenv("WHISPER_MODEL", DEFAULT_WHISPER_MODEL)
PIPER_VOICE_ID = os.getenv("PIPER_VOICE_ID", DEFAULT_PIPER_VOICE)
KOKORO_VOICE_ID = os.getenv("KOKORO_VOICE_ID", DEFAULT_KOKORO_VOICE)

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


def build_stt():
    """按 STT_ENGINE 构造语音识别服务 —— 两个引擎都本地运行、无需 key。

    SenseVoice 在中文上**同时更快更准**（asr_bench.py 实测：字错率 10.2% / 158ms，
    对比 Whisper base 的 23.8% / 607ms），因此作为默认；Whisper 保留为通用回退。
    funasr 依赖缺失时自动退回 Whisper —— 少一个可选依赖不该让服务起不来。
    """
    try:
        lang = Language(STT_LANGUAGE)
    except ValueError:
        lang = Language.ZH

    if STT_ENGINE == "sensevoice":
        try:
            from pipecat.services.funasr.stt import FunASRSTTService
        except ImportError as exc:
            logger.warning(f"[BOOT] SenseVoice 不可用（{exc}），退回 Whisper")
        else:
            return FunASRSTTService(
                settings=FunASRSTTService.Settings(
                    model="iic/SenseVoiceSmall",
                    language=lang,
                    use_itn=True,  # 把「三点」规范化为「3点」
                ),
                ttfs_p99_latency=SENSEVOICE_TTFS_P99,
            )

    return WhisperSTTService(
        settings=WhisperSTTService.Settings(
            model=WHISPER_MODEL,
            # 显式指定中文：自动猜语种既更慢也更易错（实测 607ms → 370ms）
            language=lang,
            # Whisper base 会把中文转成繁体（「介绍」亦易误识为「接收」），
            # 给一句普通话提示把它拉回简体。
            initial_prompt=WHISPER_INITIAL_PROMPT,
        ),
        # 本机实测 Whisper「说完→最终文本」约 1.0s，p99 取 1.0s。
        # 显式填入可消除 "ttfs_p99_latency not set" 告警。
        ttfs_p99_latency=WHISPER_TTFS_P99,
    )


def build_tts():
    """按 TTS_ENGINE 构造语音合成服务 —— 两者都本地运行、无需 key。

    与 ASR 不同，这里**没有又快又好的选项**，是实打实的取舍。
    同一句中文跑完整链路实测（audio_probe.py，基准 = 用户说完）：
      piper :「TTS 开始合成」→ 出声 220ms，端到端 1881ms
      kokoro:「TTS 开始合成」→ 出声 1993ms，端到端 3681ms（**慢 1800ms**）
    语音助手对「多久开口」极敏感，多等近 2 秒会明显觉得迟钝，因此默认 piper。
    想要更自然的音色就设 TTS_ENGINE=kokoro —— 但请先接受这个延迟代价。
    """
    if TTS_ENGINE == "kokoro":
        try:
            from pipecat.services.kokoro.tts import KokoroTTSService
        except ImportError as exc:
            logger.warning(f"[BOOT] Kokoro 不可用（{exc}），退回 Piper")
        else:
            return KokoroTTSService(
                settings=KokoroTTSService.Settings(
                    voice=KOKORO_VOICE_ID, language=Language.ZH
                )
            )

    return PiperTTSService(settings=PiperTTSService.Settings(voice=PIPER_VOICE_ID))


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
            "工具调用": (
                ", ".join(t.name for t in TOOLS.standard_tools) if TOOLS else "未启用"
            ),
            "故障推送前端": "已开启（ErrorObserver → RTVI error）",
            "LLM Key": _mask(os.getenv("MODELSCOPE_API_KEY")),
            "STT": (
                "SenseVoice 本地·无需 key（中文最优）"
                if STT_ENGINE == "sensevoice"
                else f"Whisper({WHISPER_MODEL}) 本地·无需 key"
            ),
            "TTS": (
                f"Piper({PIPER_VOICE_ID}) 本地·无需 key（首块 76ms）"
                if TTS_ENGINE == "piper"
                else f"Kokoro({KOKORO_VOICE_ID}) 本地·无需 key（音色好，+630ms）"
            ),
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
    # ---------- 缺 key 必须 fail-fast ----------
    # 早期版本只打一行 ERROR 然后照常启动：浏览器能连上、握手也成功，
    # 但用户一开口必然没有回应，看起来像网络故障，极难排查。
    # 这里直接终止会话并给出可操作的指引。
    if not os.getenv("MODELSCOPE_API_KEY") and not os.getenv("ALLOW_MISSING_KEY"):
        raise RuntimeError(
            "缺少 MODELSCOPE_API_KEY：LLM 调用必然失败。\n"
            "  1) 打开 server/.env 填入 MODELSCOPE_API_KEY（令牌在 "
            "https://modelscope.cn → 个人中心 → 访问令牌 获取）\n"
            "  2) 重新启动：uv run bot.py\n"
            "  只想调试前端/传输层时，可设 ALLOW_MISSING_KEY=1 跳过本检查。"
        )

    # ---------- Speech-to-Text（本地，无 key） ----------
    stt = build_stt()

    # ---------- Text-to-Speech（本地，无 key） ----------
    tts = build_tts()

    # ---------- LLM（魔搭 ModelScope，OpenAI 兼容） ----------
    llm_kwargs: dict = {
        "model": MODELSCOPE_MODEL,
        "system_instruction": SYSTEM_INSTRUCTION,
    }
    if DISABLE_THINKING:
        # pipecat 会把 OpenAILLMSettings.extra 里的键**直接作为 kwargs**
        # 传给 client.chat.completions.create(...)，因此非标准参数必须用
        # OpenAI SDK 的 extra_body 包一层，才能进入请求体 JSON。
        llm_kwargs["extra"] = build_llm_extra()

    llm = OpenAILLMService(
        api_key=os.getenv("MODELSCOPE_API_KEY"),
        base_url=MODELSCOPE_BASE_URL,
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
            # 放最后：用户侧与助手侧的文本帧都会一路传播到这里，
            # 一个 processor 就能把两边都落库
            memory.TurnRecorder(SESSION_ID),
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
        observers=[ConversationLogger(), MetricsLogObserver(), error_observer],
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
