"""运行时默认配置的唯一来源。

存在的唯一理由：README 承诺「自检测的就是真跑的配置」。
``bot.py`` 与 ``verify_stack.py`` 都需要同一批默认值，若各写一份，
任何一侧的改动都会让自检结果失真——而这种漂移不会报错，
只会让人对着错误的延迟数字做决策。

约定：
    这里只放**默认值**，真实取值仍然以环境变量为准（bot.py 里逐个 getenv）。
    自检脚本直接复用同一批默认值，因此「测的就是跑的」。
"""

from __future__ import annotations

import os

# ---------- LLM（魔搭 ModelScope，OpenAI 兼容） ----------
MODELSCOPE_BASE_URL_DEFAULT = "https://api-inference.modelscope.cn/v1"

# 三个均已实测「关闭思考后」首 token < 1 秒，默认取最快的
AVAILABLE_MODELS: tuple[str, ...] = (
    "nex-agi/Nex-N2.5-mini",  # 710 ms  ← 默认
    "Qwen/Qwen3.8-Flash-Next",  # 787 ms
    "deepseek-ai/DeepSeek-V4.1-Flash",  # 817 ms
)
DEFAULT_MODEL = AVAILABLE_MODELS[0]

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


def build_llm_extra() -> dict:
    """构造传给 OpenAILLMService.Settings 的 extra。

    pipecat 会把 Settings.extra 里的键**直接作为 kwargs** 传给
    ``client.chat.completions.create(...)``，因此非标准参数必须用 OpenAI SDK
    的 ``extra_body`` 包一层，否则会被当成未知关键字参数报错。
    """
    return {"extra_body": {"enable_thinking": False}}
