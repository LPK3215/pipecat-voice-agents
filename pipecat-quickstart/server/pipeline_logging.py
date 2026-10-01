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
    [ERROR]  管线故障（处理器 / 错误类别 / 是否还能用），并同步推送给前端
    [TOOL]   工具调用的完整记录（模型要求调用 / 开始执行 / 结果 / 失败 / 超时 / 取消）

关于 [TOOL] 日志的规范做法（有教训，改前必读）：
    工具调用**一律走框架自带的 ``FunctionCallObserver``**，不要自己监听了
    四个工具帧手写一套。本项目早期就是这么干的，结果同时踩了三个坑：
      1) 漏了结局：手写版只区分「成功/失败」，而框架区分
         started / in_progress / completed / failed / timed_out / cancelled 六种，
         漏掉了超时与被取消 —— 而「调用了却永远没结果」正是最该查的情况；
      2) 广播帧重复：四个工具帧都由 ``broadcast_frame`` 发出，上行下行各一个实例，
         两个实例的 ``frame.id`` 不同、都会被判定为首次推送，于是每条记两遍。
         框架里的处理方式是只读 DOWNSTREAM 那一个
         （``frame.broadcast_sibling_id is not None and direction != DOWNSTREAM → return``），
         自己写就得重新发现一遍这个约定；
      3) 耗时靠外部推断：框架把 ``started_at`` / ``in_progress_at`` 直接写进事件，
         一条记录就能读出「排队等了多久 + 执行了多久」两段时间。
    ⚠️ ``FunctionCallObserver`` 的 ``include_results`` **默认是 False**（只记参数不记结果），
       排查问题必须显式传 True，否则等于少了一半信息。

注意：pipecat 的 runner 启动时会调用 ``logger.remove()`` 清空所有日志出口
（pipecat/runner/run.py），因此**会话建立后需要再次调用 ``setup_logging``**。
"""

import json
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
from pipecat.observers.error_observer import ErrorObserver
from pipecat.observers.function_call_observer import (
    FunctionCallEventKind,
    FunctionCallObserver,
)
from pipecat.utils.errors import ErrorCategory

SERVER_DIR = Path(__file__).resolve().parent
LOG_DIR = SERVER_DIR / "logs"

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
    # 本次运行的历史文件用追加（同一进程内 setup_logging 会被调用多次，
    # 用 "w" 会把本次开头的 [BOOT] 记录冲掉）。
    logger.add(run_log, level="DEBUG", format=FILE_FORMAT, encoding="utf-8")
    # latest 必须截断：loguru 默认以追加方式打开文件，
    # 否则它会无限累积历次运行的内容，「永远指向最近一次运行」就是假的。
    logger.add(latest, level="DEBUG", format=FILE_FORMAT, encoding="utf-8", mode="w")
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
# 故障上报：日志 + 前端可见
# ---------------------------------------------------------------------------
# 把 pipecat 的错误类别翻译成人（和前端用户）能懂的话。
# 分类与具体供应商无关，因此换 LLM / STT 厂商这张表依然成立。
ERROR_HINTS: dict[ErrorCategory, str] = {
    ErrorCategory.AUTHENTICATION: "密钥无效或缺失",
    ErrorCategory.AUTHORIZATION: "密钥无权访问该资源",
    ErrorCategory.INVALID_REQUEST: "请求被服务端拒绝（参数或消息格式不被接受）",
    ErrorCategory.RATE_LIMIT: "调用过于频繁，被限流",
    ErrorCategory.QUOTA: "账号额度已用尽",
    ErrorCategory.CONNECTIVITY: "无法连接服务，请检查网络",
    ErrorCategory.SERVER: "服务端内部错误",
    ErrorCategory.APPLICATION: "应用代码异常",
    ErrorCategory.UNKNOWN: "发生未知错误",
}


def install_error_reporting(worker, observer: ErrorObserver | None = None) -> ErrorObserver:
    """把管线故障同时写进日志，并推给前端显示。

    为什么需要它：
        服务失败时浏览器端原本毫无提示 —— 连接是成功的、握手也是成功的，
        只有用户开口之后才发现没人应答，极容易被误判成网络故障。
        pipecat 的 ``ErrorObserver`` 在**错误发生的源头**就抓到它
        （而不是等它传到管线末端，中途可能已被别的处理器消化掉），
        并给出 processor / 错误类别 / 异常类型 / 该处理器是否还能用。

    前端无需改动：``rtvi.send_error()`` 发出的是 ``error`` 消息，
    已在官方 Prebuilt 前端的 RTVI 消息表中。

    Args:
        worker: 管线 worker，用来取 ``worker.rtvi``。
        observer: 复用已有观察者；不传则新建一个。

    Returns:
        ErrorObserver —— 必须把它加进 ``PipelineWorker(observers=[...])`` 才生效。
    """
    if observer is None:
        observer = ErrorObserver()

    # 同一个处理器的同一类错误只推一次：否则限流或断网会把前端刷屏
    sent: set[tuple[str, str]] = set()

    @observer.event_handler("on_error")
    async def _on_error(_observer, event):
        hint = ERROR_HINTS.get(event.category, ERROR_HINTS[ErrorCategory.UNKNOWN])
        scope = "服务已不可用" if not event.processor_usable else "单次失败"
        logger.error(
            f"[ERROR] {event.processor} | {event.category.value} | {scope} | "
            f"异常={event.exception_type or '-'} | {event.message}"
        )

        rtvi = getattr(worker, "rtvi", None)
        if rtvi is None:
            return
        key = (event.processor, event.category.value)
        if key in sent:
            return
        sent.add(key)
        try:
            await rtvi.send_error(f"{hint}（{event.processor}）")
            logger.info(f"[ERROR] 已推送前端: {hint}（{event.processor}）")
        except Exception as exc:  # noqa: BLE001 - 推不动前端不能影响主流程
            logger.warning(f"[ERROR] 推送前端失败: {exc}")

    return observer


# ---------------------------------------------------------------------------
# 工具调用记录（按框架推荐方式）
# ---------------------------------------------------------------------------

# 框架把工具调用分成六种结局，这里翻译成中文。
# 前两种是「开始」，后四种是「结局」—— 结局里除 completed 外都属于异常路径，
# 尤其 timed_out / cancelled 是「调了却没有结果」的元凶，必须能一眼看到。
_FUNCTION_CALL_LABELS = {
    FunctionCallEventKind.STARTED: "◆ 模型要求调用",
    FunctionCallEventKind.IN_PROGRESS: "▶ 开始执行",
    FunctionCallEventKind.COMPLETED: "◀ 执行完成",
    FunctionCallEventKind.FAILED: "✖ 执行失败",
    FunctionCallEventKind.TIMED_OUT: "⏱ 执行超时",
    FunctionCallEventKind.CANCELLED: "⊘ 调用被取消",
}


def _render_payload(payload) -> str:
    """把参数/结果渲染成完整字符串。**不截断**。

    JSON 化而不是直接 str()：既保留嵌套结构，也避免 dict 的单引号写法
    在日志里难以复制复用。``default=str`` 兜住不可序列化的值。
    """
    try:
        return json.dumps(payload, ensure_ascii=False, default=str, indent=2)
    except (TypeError, ValueError):
        return repr(payload)


def install_function_call_logging(
    observer: FunctionCallObserver | None = None,
) -> FunctionCallObserver:
    """记录每一次工具调用：按框架推荐方式，用自带的 ``FunctionCallObserver``。

    为什么不自己写（本项目踩过的坑，详见模块文档字符串）：
        手写版只区分成功/失败，漏掉超时与被取消；还要自行处理广播帧重复；
        耗时也得自己从帧里推断。框架都做好了。

    ⚠️ 必须显式传 ``include_results=True`` —— 它的默认值是 ``False``
       （只记参数、不记结果），排查问题时等于少了一半信息。

    Args:
        observer: 复用已有观察者；不传则新建一个。

    Returns:
        观察者实例 —— 必须加进 ``PipelineWorker(observers=[...])`` 才生效。
    """
    if observer is None:
        observer = FunctionCallObserver(include_results=True)

    @observer.event_handler("on_function_call_event")
    async def _on_function_call_event(_observer, event):
        label = _FUNCTION_CALL_LABELS.get(event.kind, event.kind.value)
        lines = [
            f"[TOOL] {label} {event.function_name}（id={event.tool_call_id}）"
        ]

        # 排队与执行两段时间，由框架写在事件里，无需自己记时钟
        now = getattr(event, "timestamp", None)
        if event.started_at and event.in_progress_at and event.in_progress_at >= event.started_at:
            lines.append(
                f"[TOOL]     排队等待 {(event.in_progress_at - event.started_at) * 1000:.0f}ms"
            )
        if now and event.in_progress_at and now >= event.in_progress_at:
            lines.append(f"[TOOL]     执行耗时 {(now - event.in_progress_at) * 1000:.0f}ms")

        if event.arguments is not None:
            lines.append(f"[TOOL]     参数: {_render_payload(event.arguments)}")
        if event.result is not None:
            lines.append(f"[TOOL]     结果: {_render_payload(event.result)}")
        if event.error is not None:
            lines.append(f"[TOOL]     错误: {event.error}")
        if event.blocking is not None:
            lines.append(
                "[TOOL]     是否阻塞对话: "
                f"{'是，等它返回才继续' if event.blocking else '否，稍后异步回填'}"
            )

        text = "\n".join(lines)
        # 异常结局用 warning 级：查「为什么没回答」时可以直接过滤出来
        if event.kind in (
            FunctionCallEventKind.FAILED,
            FunctionCallEventKind.TIMED_OUT,
            FunctionCallEventKind.CANCELLED,
        ):
            logger.warning(text)
        else:
            logger.info(text)

    return observer


# ---------------------------------------------------------------------------
# 对话时间线观察者
# ---------------------------------------------------------------------------
class ConversationLogger(BaseObserver):
    """把管线帧翻译成人能读的对话时间线，并统计每轮分段延迟。

    去重交给框架：pipecat 的 ``BaseObserver`` 提供 ``observe_every_push=False``，
    只在帧**首次**被推送时通知观察者。

    早期版本是自建 ``frame.id`` 集合去重的，这里不再需要，原因是：
      - 手写集合随会话无限增长，只能靠「超限后整体清空」兜底；
        而清空会把仍在管线中的帧重新判定为未见过，导致同一帧重复打日志。
      - 框架的去重按推送次数判定，没有这两个问题。
    """

    def __init__(self, **kwargs):
        # 只在首跳观察：避免同一帧经过 N 个处理器就被记录 N 次
        kwargs.setdefault("observe_every_push", False)
        super().__init__(**kwargs)
        self._turn_t0: float | None = None
        self._marks: dict[str, float] = {}
        self._reply: list[str] = []
        self._turns = 0

    async def on_push_frame(self, data) -> None:
        frame = data.frame
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

        # 工具调用不在这里记录 —— 交给框架自带的 FunctionCallObserver
        # （见 install_function_call_logging：它区分六种结局，并已处理广播重复）。

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
        """完整渲染发给 LLM 的上下文，**不截断**。

        早期版本只保留最后 600 字符，会把最前面的系统提示词与注入的
        长期记忆切掉 —— 而那两段恰恰是排查「模型为什么这么答」最需要看的。
        现在逐条完整列出，并附上本轮实际可用的工具清单：
        模型选错工具时，第一件要确认的就是「它当时到底看得到哪些工具」。
        """
        context = getattr(frame, "context", None)
        messages = getattr(context, "messages", None) or context
        try:
            items = list(messages)
        except TypeError:
            return repr(frame)

        lines = [f"（共 {len(items)} 条消息）"]
        for i, msg in enumerate(items, 1):
            if isinstance(msg, dict):
                role, content = msg.get("role", "?"), msg.get("content", "")
            else:
                role = getattr(msg, "role", "?")
                content = getattr(msg, "content", msg)
            # 带 tool_calls 的助手消息也要完整展示，否则「模型调了什么」这一段是空的
            calls = msg.get("tool_calls") if isinstance(msg, dict) else None
            suffix = f"  tool_calls={json.dumps(calls, ensure_ascii=False, default=str)}" if calls else ""
            lines.append(f"  {i}. [{role}] {content}{suffix}")

        try:
            tools = getattr(context, "tools", None)
            names = [t.name for t in getattr(tools, "standard_tools", [])]
        except Exception:  # noqa: BLE001 - 工具清单只是附加信息，取不到不该影响上下文输出
            names = []
        lines.append(f"  本轮可用工具（{len(names)} 个）: {names}")
        return "\n".join(lines)

    def _report_turn(self) -> None:
        base = self._marks.get("说完")
        if base is None:
            return
        rows = []
        for name in ("识别文本", "发往LLM", "首个答案token", "首次出声"):
            ts = self._marks.get(name)
            rows.append(f"{name} {(ts - base) * 1000:.0f}ms" if ts else f"{name} N/A")
        logger.info("[TURN] 分段延迟（基准=说完）: " + " | ".join(rows))
