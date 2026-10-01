"""统一可观测性：前端事件、后端帧、LLM 请求与回复、每轮延迟 —— 全部落盘。

设计目标（一次运行即可定位问题）：
    跑一次就能从日志看出「谁在什么时候说了什么、等了多久、出了什么错」。

产出两个文件：
    logs/<prefix>-<时间戳>.log   本次运行的完整历史
    logs/<prefix>-latest.log     固定名，永远指向最近一次运行

日志前缀约定：
    [BOOT]   启动配置（模型 / STT / TTS / VAD / 传输 / 密钥状态）
    [CLIENT] 前端事件（连接、断开、RTVI 消息、客户端信息）
    [TURN]   一轮对话的时间线与分段延迟
    [FRAME]  关键帧流水（DEBUG 级，用于排查）

注意：pipecat 的 runner 启动时会调用 ``logger.remove()`` 清空所有日志出口
（pipecat/runner/run.py），因此**会话建立后需要再次调用 ``setup_logging``**。
"""

import sys
import time
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver

SERVER_DIR = Path(__file__).resolve().parent
LOG_DIR = SERVER_DIR / "logs"
_MAX_SEEN = 20000  # 去重表上限，防止长会话内存无限增长

CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <7}</level> | <level>{message}</level>"
)
FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | {name}:{function}:{line} | {message}"


# ---------------------------------------------------------------------------
# 日志出口
# ---------------------------------------------------------------------------
def resolve_log_paths(prefix: str = "bot") -> tuple[Path, Path]:
    """只计算日志路径，不挂出口（路径只算一次，全程复用）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return LOG_DIR / f"{prefix}-{stamp}.log", LOG_DIR / f"{prefix}-latest.log"


def setup_logging(
    run_log: Path | None = None,
    latest: Path | None = None,
    prefix: str = "bot",
) -> tuple[Path, Path]:
    """挂上日志出口：控制台 INFO + 两个文件出口（DEBUG，全量）。

    Returns:
        (本次运行的日志文件, 固定名 latest 日志文件)
    """
    if run_log is None or latest is None:
        run_log, latest = resolve_log_paths(prefix)

    logger.remove()
    logger.add(sys.stderr, level="INFO", format=CONSOLE_FORMAT, colorize=True)
    # 全量落盘：DEBUG 级，包含 pipecat 内部所有细节
    logger.add(run_log, level="DEBUG", format=FILE_FORMAT, encoding="utf-8")
    logger.add(latest, level="DEBUG", format=FILE_FORMAT, encoding="utf-8")
    return run_log, latest


def log_boot_banner(config: dict) -> None:
    """把「本次实际生效的配置」打印出来 —— 避免配置漂移导致误判。"""
    logger.info("[BOOT] " + "=" * 60)
    logger.info("[BOOT] 本次运行的实际配置")
    for key, value in config.items():
        logger.info(f"[BOOT]   {key:<16}= {value}")
    logger.info("[BOOT] " + "=" * 60)


# ---------------------------------------------------------------------------
# 前端（浏览器）事件
# ---------------------------------------------------------------------------
def install_client_logging(transport, rtvi) -> None:
    """挂上前端侧事件的日志。"""
    if rtvi is not None:

        @rtvi.event_handler("on_client_ready")
        async def _on_client_ready(_rtvi):
            logger.info("[CLIENT] 前端就绪（RTVI on_client_ready）")

        @rtvi.event_handler("on_client_message")
        async def _on_client_message(_rtvi, message):
            logger.info(f"[CLIENT] 收到前端消息: {message}")

        @rtvi.event_handler("on_ui_message")
        async def _on_ui_message(_rtvi, message):
            logger.debug(f"[CLIENT] UI 消息: {message}")

        @rtvi.event_handler("on_bot_started")
        async def _on_bot_started(_rtvi):
            logger.info("[CLIENT] 机器人会话已开始（on_bot_started）")

    @transport.event_handler("on_client_connected")
    async def _on_connected(_transport, client):
        logger.info(f"[CLIENT] 浏览器已连接 | {_describe(client)}")

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_transport, client):
        logger.info(f"[CLIENT] 浏览器已断开 | {_describe(client)}")


def _describe(client) -> str:
    """尽量把前端客户端对象里能读的信息都打出来（含用户标识）。"""
    if client is None:
        return "client=unknown"
    parts = []
    for attr in ("id", "client_id", "user_id", "name", "platform", "version"):
        value = getattr(client, attr, None)
        if value:
            parts.append(f"{attr}={value}")
    if not parts:
        parts.append(f"repr={repr(client)[:120]}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 对话时间线观察者
# ---------------------------------------------------------------------------
class ConversationLogger(BaseObserver):
    """把管线帧翻译成人能读的对话时间线，并统计每轮分段延迟。

    关键：同一帧会在每一跳都被观察到，因此用 frame.id 去重，只记录首次。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._seen: set = set()
        self._turn_t0: float | None = None
        self._marks: dict[str, float] = {}
        self._reply: list[str] = []
        self._turns = 0

    async def on_push_frame(self, data) -> None:
        frame = data.frame
        fid = getattr(frame, "id", None)
        if fid is not None:
            if fid in self._seen:
                return
            self._seen.add(fid)
            if len(self._seen) > _MAX_SEEN:
                self._seen.clear()

        now = time.perf_counter()

        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._start_turn(now)
            logger.debug("[FRAME] VAD: 用户开始说话")

        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._mark("说完", now)
            logger.info("[TURN] 用户停止说话（VAD 判定说完）")

        elif isinstance(frame, TranscriptionFrame):
            self._mark("识别文本", now)
            logger.info(f"[TURN] 识别文本: {frame.text!r}")

        elif isinstance(frame, LLMContextFrame):
            self._mark("发往LLM", now)
            logger.info(f"[TURN] → 发往 LLM 的上下文: {self._render_context(frame)}")

        elif isinstance(frame, LLMFullResponseStartFrame):
            self._reply = []
            logger.debug("[FRAME] LLM 开始输出")

        elif isinstance(frame, LLMTextFrame):
            self._mark("首个答案token", now)
            self._reply.append(getattr(frame, "text", "") or "")

        elif isinstance(frame, LLMFullResponseEndFrame):
            logger.info(f"[TURN] ← LLM 回答完毕: {''.join(self._reply)!r}")

        elif isinstance(frame, TTSAudioRawFrame):
            if "首次出声" not in self._marks:
                self._mark("首次出声", now)
                logger.info("[TURN] TTS 开始出声")
                self._report_turn()

        elif isinstance(frame, BotStartedSpeakingFrame):
            logger.debug("[FRAME] 机器人开始说话")

        elif isinstance(frame, BotStoppedSpeakingFrame):
            logger.debug("[FRAME] 机器人说完")

        elif isinstance(frame, ErrorFrame):
            logger.error(f"[FRAME] 管线错误: {frame.error}")

        elif isinstance(frame, UserStoppedSpeakingFrame):
            logger.debug("[FRAME] 轮次结束标记 UserStoppedSpeaking")

    # ---------------- 内部 ----------------
    def _start_turn(self, now: float) -> None:
        self._turn_t0 = now
        self._marks = {}
        self._reply = []
        self._turns += 1
        logger.info(f"[TURN] ─────── 第 {self._turns} 轮对话开始 ───────")

    def _mark(self, name: str, now: float) -> None:
        if self._turn_t0 is None or name in self._marks:
            return
        self._marks[name] = now

    @staticmethod
    def _render_context(frame) -> str:
        context = getattr(frame, "context", None)
        messages = getattr(context, "messages", None) or context
        try:
            return str(list(messages))[-600:]
        except Exception:  # noqa: BLE001
            return repr(frame)[:300]

    def _report_turn(self) -> None:
        base = self._marks.get("说完")
        if base is None:
            return
        rows = []
        for name in ("识别文本", "发往LLM", "首个答案token", "首次出声"):
            ts = self._marks.get(name)
            rows.append(f"{name} {(ts - base) * 1000:.0f}ms" if ts else f"{name} N/A")
        logger.info("[TURN] 分段延迟（基准=说完）: " + " | ".join(rows))
