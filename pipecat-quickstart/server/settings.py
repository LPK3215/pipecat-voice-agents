"""运行时默认配置的唯一来源。

存在的唯一理由：README 承诺「自检测的就是真跑的配置」。
``bot.py`` 与 ``verify_stack.py`` 都需要同一批默认值，若各写一份，
任何一侧的改动都会让自检结果失真——而这种漂移不会报错，
只会让人对着错误的延迟数字做决策。

约定：
    这里放**默认值**，以及两个本地服务的**构造逻辑**（``build_stt`` / ``build_tts``）。
    真实取值仍然以环境变量为准（bot.py 里逐个 getenv）。
    bot.py 与 verify_stack.py 复用同一批默认值与同一套构造逻辑，因此「测的就是跑的」。
"""

from __future__ import annotations

import os

from loguru import logger

# ---------- LLM 服务商（OpenAI 兼容，可切换） ----------
# 两个服务商都用同一个 OpenAILLMService，切换只改 base_url / key / model。
# 默认 modelscope（保持既有行为）；设 LLM_PROVIDER=suanli 即切到共绩 MaaS。
LLM_PROVIDERS: dict[str, dict] = {
    "modelscope": {
        "base_url": "https://api-inference.modelscope.cn/v1",
        "base_url_env": "MODELSCOPE_BASE_URL",
        "api_key_env": "MODELSCOPE_API_KEY",
        "model_env": "MODELSCOPE_MODEL",
        "default_model": "nex-agi/Nex-N2.5-mini",
        # 三个均已实测「关闭思考后」首 token < 1 秒，默认取最快的
        "recommended": (
            "nex-agi/Nex-N2.5-mini",  # 710 ms  ← 默认
            "Qwen/Qwen3.8-Flash-Next",  # 787 ms
            "deepseek-ai/DeepSeek-V4.1-Flash",  # 817 ms
        ),
        # 「关思考」参数各家写法不同：魔搭用 enable_thinking
        "thinking_body": {"enable_thinking": False},
    },
    "sensenova": {
        # 商汤日日新 SenseNova（OpenAI 兼容）。key 形如 sk-...
        "base_url": "https://token.sensenova.cn/v1",
        "base_url_env": "SENSENOVA_BASE_URL",
        "api_key_env": "SENSENOVA_API_KEY",
        "model_env": "SENSENOVA_MODEL",
        "default_model": "sensenova-6.8-flash-lite",
        "recommended": (),
        # 实测：enable_thinking / chat_template_kwargs 都无效，只有
        # thinking={"type":"disabled"} 才真正关掉思考（否则首 token 全耗在 reasoning 上）
        "thinking_body": {"thinking": {"type": "disabled"}},
    },
    "suanli": {
        # 共绩算力 MaaS（OpenAI 兼容）。只需 key + base_url，换模型只改 model。
        # 注意是 api.suanli.cn，不是算力 Open API 的 openapi.suanli.cn（那是两套东西）
        "base_url": "https://api.suanli.cn/v1",
        "base_url_env": "SUANLI_BASE_URL",
        "api_key_env": "SUANLI_API_KEY",
        "model_env": "SUANLI_MODEL",
        # 共绩的 model ID 形如「厂商/模型」，以控制台/模型广场为准。
        "default_model": "qwen/qwen3.8-27b",
        "recommended": (),
        # 未验证其关思考参数，暂不注入（避免 400）
        "thinking_body": None,
    },
}
DEFAULT_PROVIDER = "modelscope"

# 兼容旧名（自检脚本 / 文档仍可能引用）
MODELSCOPE_BASE_URL_DEFAULT = LLM_PROVIDERS["modelscope"]["base_url"]
AVAILABLE_MODELS: tuple[str, ...] = LLM_PROVIDERS["modelscope"]["recommended"]
DEFAULT_MODEL = LLM_PROVIDERS[DEFAULT_PROVIDER]["default_model"]

# 关闭「思考」模式：Qwen / DeepSeek 默认先推理再回答，实测首 token 2.5s → 0.8s
DISABLE_THINKING_DEFAULT = "1"

# 是否把工具（function calling）开放给 LLM。前端无需改动即可展示调用过程
ENABLE_TOOLS_DEFAULT = "1"

# 环境变量里这些值一律表示「关闭」
_OFF_SENTINELS = ("0", "false", "False")

DEFAULT_SYSTEM_INSTRUCTION = (
    "你是一个语音助手，正在进行语音对话。你的回答会被朗读出来，"
    "所以请口语化、简短，不要使用 emoji、markdown、列表等无法朗读的格式。"
)
DEFAULT_OPENING_MESSAGE = "先用一句话简短地自我介绍。"

# ---------- STT / TTS（均为本地，无 key） ----------
# 官方默认 Systran/faster-distil-whisper-medium.en 是【纯英文】模型，中文须用多语种
DEFAULT_WHISPER_MODEL = "base"
DEFAULT_PIPER_VOICE = "zh_CN-huayan-medium"

# Whisper base 倾向输出繁体；给一句普通话提示把它拉回简体（实测遗留问题）
WHISPER_INITIAL_PROMPT = "以下是普通话的句子。"

# 本机实测 Whisper「说完→最终文本」0.66~0.78s，p99 取 1.0s。
# 显式填入可消除 "ttfs_p99_latency not set" 告警。
WHISPER_TTFS_P99 = 1.0

# ---------- STT 引擎 ----------
# 两者都是**本地、免费、无需 key**，但中文表现差距很大（asr_bench.py 实测，6 句测试集）：
#   sensevoice：字错率 10.2%，158ms/句 —— 非自回归，专为中文等多语种设计，又快又准
#   whisper   ：字错率 23.8%，607ms/句（base）—— 通用但中文弱，且倾向输出繁体
# 选 sensevoice 可在**准确率与延迟上同时变好**，不是取舍。
DEFAULT_STT_ENGINE = "sensevoice"

# 显式指定中文：Whisper 自动猜语种时既更慢也更易错（实测 607ms → 370ms）
DEFAULT_STT_LANGUAGE = "zh"

# SenseVoice 单句实测 158ms，p99 取 0.5s 留足余量
SENSEVOICE_TTFS_P99 = 0.5

# ---------- TTS 引擎 ----------
# 两者都本地免费，但**首个音频块**的延迟差距很大（同一句中文实测）：
#   piper : 76ms  —— 音色偏机械，几乎不增加延迟
#   kokoro: 709ms —— 音色明显自然（8 个中文音色），代价是 +630ms
# 语音助手对「多久开口」很敏感，因此默认 piper；想要更好音色设 TTS_ENGINE=kokoro。
# 注意这是**真实的取舍**：不像 ASR 那样能又快又好。
DEFAULT_TTS_ENGINE = "piper"
DEFAULT_KOKORO_VOICE = "zf_xiaoxiao"

# ---------- VAD ----------
# 官方默认 0.2s 会把一句中文按逗号停顿切成两段，导致 LLM 只收到半句
DEFAULT_VAD_STOP_SECS = 0.6
VAD_STOP_SECS_OFFICIAL_DEFAULT = 0.2  # pipecat 官方推荐值，仅用于日志提示

# ---------- 自检脚本使用的固定测试句 ----------
VERIFY_USER_TEXT = "你好，请用一句话介绍一下你自己。"


def thinking_disabled(value: str | None = None) -> bool:
    """是否关闭模型「思考」模式。默认开启（即关闭思考）。"""
    raw = value if value is not None else os.getenv("LLM_DISABLE_THINKING", DISABLE_THINKING_DEFAULT)
    return raw not in _OFF_SENTINELS


def tools_enabled(value: str | None = None) -> bool:
    """是否开放工具调用给 LLM。默认开启。"""
    raw = value if value is not None else os.getenv("ENABLE_TOOLS", ENABLE_TOOLS_DEFAULT)
    return raw not in _OFF_SENTINELS


def stt_engine(value: str | None = None) -> str:
    """语音识别引擎：``sensevoice``（默认，中文最优）或 ``whisper``。

    传入无法识别的值时静默退回默认引擎 —— 引擎写错不该让服务起不来。
    """
    raw = (
        value if value is not None else os.getenv("STT_ENGINE", DEFAULT_STT_ENGINE)
    ).strip().lower()
    return raw if raw in ("sensevoice", "whisper") else DEFAULT_STT_ENGINE


def tts_engine(value: str | None = None) -> str:
    """语音合成引擎：``piper``（默认，延迟低）或 ``kokoro``（音色好但慢 630ms）。"""
    raw = (
        value if value is not None else os.getenv("TTS_ENGINE", DEFAULT_TTS_ENGINE)
    ).strip().lower()
    return raw if raw in ("piper", "kokoro") else DEFAULT_TTS_ENGINE


def llm_provider(name: str | None = None) -> str:
    """解析当前 LLM 服务商名（``modelscope`` 或 ``suanli``）。

    未知值静默退回默认 —— 拼错服务商不该让服务起不来。
    """
    raw = (name or os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER)).strip().lower()
    return raw if raw in LLM_PROVIDERS else DEFAULT_PROVIDER


def llm_config(provider: str | None = None) -> dict:
    """解析当前服务商的 base_url / key / model 等，供 bot 与自检脚本共用。

    两个服务商都是 OpenAI 兼容，因此 bot 用的是同一个 ``OpenAILLMService``，
    切换只改这里的解析结果。默认 modelscope，保持既有行为不变。
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
    """构造传给 OpenAILLMService.Settings 的 extra。

    pipecat 会把 Settings.extra 里的键**直接作为 kwargs** 传给
    ``client.chat.completions.create(...)``，因此非标准参数必须用 OpenAI SDK
    的 ``extra_body`` 包一层，否则会被当成未知关键字参数报错。

    ``thinking_body``：各家「关思考」的写法不同，由调用方按服务商传入
    （见 ``LLM_PROVIDERS[..]["thinking_body"]``）：
        魔搭 modelscope  ``{"enable_thinking": False}``
        商汤 sensenova   ``{"thinking": {"type": "disabled"}}``（实测唯一有效的写法）
        共绩 suanli      ``None`` —— 未验证，不注入以免 400
    ``None`` 表示不注入任何关思考参数。

    ``LLM_TEMPERATURE``：采样温度。**不设置时用服务端默认值**（通常很高），
    在工具选择这种「要么调、要么不调」的离散决策上会表现为**每次结果不同**：
    实测同一问题、同一套工具，重复 5 次只有 2 次调用了正确的工具。
    需要稳定复现时把它设为 0（代价是回答更死板、多样性下降）。

    没有任何要注入的键时返回 ``{}`` —— 不要塞一个空的 ``extra_body``。
    """
    body: dict = dict(thinking_body) if thinking_body else {}

    temperature = os.getenv("LLM_TEMPERATURE")
    if temperature not in (None, ""):
        body["temperature"] = float(temperature)

    return {"extra_body": body} if body else {}


# ---------------------------------------------------------------------------
# 本地服务构造（bot.py 与 verify_stack.py 共用）
#
# 都返回 ``(service, 说明)``。说明里写的是**实际生效**的引擎：
# 配置为 sensevoice / kokoro 但依赖缺失时会降级为 whisper / piper，
# 此时说明必须如实反映，否则「BOOT 横幅 = 实际配置」这一承诺就是假的。
# ---------------------------------------------------------------------------


def stt_desc(engine: str) -> str:
    """STT 引擎的人类可读描述；构造服务与打印横幅共用，避免两处文案漂移。

    ``engine`` 传**实际生效**的引擎名（可能由 sensevoice 降级为 whisper）。
    """
    if engine == "sensevoice":
        return "SenseVoice(iic/SenseVoiceSmall) 本地·无需 key（中文最优）"
    model = os.getenv("WHISPER_MODEL", DEFAULT_WHISPER_MODEL)
    return f"Whisper({model}) 本地·无需 key"


def tts_desc(engine: str) -> str:
    """TTS 引擎的人类可读描述；``engine`` 传**实际生效**的引擎名。"""
    if engine == "kokoro":
        voice = os.getenv("KOKORO_VOICE_ID", DEFAULT_KOKORO_VOICE)
        return f"Kokoro({voice}) 本地·无需 key（音色好，+630ms）"
    voice = os.getenv("PIPER_VOICE_ID", DEFAULT_PIPER_VOICE)
    return f"Piper({voice}) 本地·无需 key（首块 76ms）"


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
    """构造本地 STT 服务。

    两个引擎都本地运行、无需 key；SenseVoice 在中文上**同时更快更准**
    （asr_bench.py 实测：字错率 10.2% / 158ms，对比 Whisper base 的 23.8% / 607ms），
    因此作为默认；Whisper 保留为通用回退。funasr 依赖缺失时自动退回 Whisper ——
    少一个可选依赖不该让服务起不来，但降级必须写进日志与返回的说明里。
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
                f"[BOOT] SenseVoice 不可用（{exc}），已退回 Whisper（中文准确率会下降）。"
                "安装依赖即可启用（见 HANDBOOK.md 第 2 节）："
                "uv pip install torch torchaudio --index-url "
                "https://download.pytorch.org/whl/cpu && uv pip install funasr"
            )
        else:
            service = FunASRSTTService(
                settings=FunASRSTTService.Settings(
                    model="iic/SenseVoiceSmall",
                    language=lang,
                    use_itn=True,  # 把「三点」规范化为「3点」
                ),
                ttfs_p99_latency=SENSEVOICE_TTFS_P99,
            )
            return service, stt_desc("sensevoice")

    service = WhisperSTTService(
        settings=WhisperSTTService.Settings(
            model=model,
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
    return service, stt_desc("whisper")


def build_tts(
    engine: str | None = None,
    piper_voice: str | None = None,
    kokoro_voice: str | None = None,
):
    """构造本地 TTS 服务。

    与 ASR 不同，这里**没有又快又好的选项**，是实打实的取舍：
    同一句中文的首个音频块 Piper 约 76ms、Kokoro 约 709ms（+630ms）。
    语音助手对「多久开口」极敏感，因此默认 Piper；想要更自然的音色再设 kokoro。
    """
    from pipecat.services.piper.tts import PiperTTSService

    eng = tts_engine(engine)

    if eng == "kokoro":
        try:
            from pipecat.services.kokoro.tts import KokoroTTSService
        except ImportError as exc:
            logger.warning(f"[BOOT] Kokoro 不可用（{exc}），已退回 Piper")
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
