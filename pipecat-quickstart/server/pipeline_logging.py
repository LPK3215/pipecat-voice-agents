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
    [TOOL]   工具调用的完整记录（模型原始决定 / 参数 / 结果 / 耗时 / 成功失败 / 被取消）

关于 [TOOL] 日志的完整性约定：
    工具调用的**权威记录**在本模块统一输出，而不是散落在各工具实现里。
    原因是各工具自己打日志必然出现三种缺漏：格式不一、内容截断、
    以及「模型调了但工具没打日志」的死角。
    这里直接监听框架的四个工具帧，因此**不论哪个工具、不论成功失败、
    不论是否被用户打断，都一定有记录**，且参数与结果原样完整输出、不做截断。

注意：pipecat 的 runner 启动时会调用 ``logger.remove()`` 清空所有日志出口
（pipecat/runner/run.py），因此**会话建立后需要再次调用 ``setup_logging``**。
"""

import json
import sys
import time
from collections import deque
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    FunctionCallCancelFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
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
        # 工具执行起始时刻，按 tool_call_id 记录，用于算单个工具的真实耗时
        self._tool_t0: dict[str, float] = {}
        # 广播帧去重用。有界，不会随会话无限增长（见 _is_duplicate_broadcast）
        self._seen_broadcast: set[str] = set()
        self._seen_order: deque[str] = deque(maxlen=512)

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

        # ---- 工具调用：完整记录 ----
        # 监听框架的四个工具帧，因此任何工具、成功或失败、是否被打断，都必有记录。
        # 内容一律原样输出，不截断 —— 截断过的日志在排查时等于没有。
        # 这四个帧都是由 broadcast_frame 发出的，必须先过广播去重，
        # 否则每一帧都会被记录两遍（原因见 _is_duplicate_broadcast）。
        elif isinstance(frame, FunctionCallsStartedFrame):
            if self._is_duplicate_broadcast(frame):
                return
            logger.info(f"[TOOL] ◆ 模型决定调用 {len(frame.function_calls)} 个工具")
            for call in frame.function_calls:
                logger.info(
                    f"[TOOL] ◆   调用 {call.function_name}"
                    f"（id={call.tool_call_id}）\n"
                    f"[TOOL] ◆   参数: {self._render_payload(call.arguments)}"
                )

        elif isinstance(frame, FunctionCallInProgressFrame):
            if self._is_duplicate_broadcast(frame):
                return
            self._tool_t0[frame.tool_call_id] = now
            logger.info(
                f"[TOOL] ▶ 开始执行 {frame.function_name}"
                f"（id={frame.tool_call_id}）\n"
                f"[TOOL] ▶   参数: {self._render_payload(frame.arguments)}\n"
                f"[TOOL] ▶   用户打断时: "
                f"{'取消本次调用' if frame.cancel_on_interruption else '不取消，执行到底'}"
            )

        elif isinstance(frame, FunctionCallResultFrame):
            if self._is_duplicate_broadcast(frame):
                return
            cost = now - self._tool_t0.pop(frame.tool_call_id, now)
            logger.info(
                f"[TOOL] ◀ {frame.function_name} "
                f"{'执行失败' if frame.error else '执行成功'}"
                f"（耗时 {cost * 1000:.0f}ms，id={frame.tool_call_id}）\n"
                f"[TOOL] ◀   参数: {self._render_payload(frame.arguments)}\n"
                f"[TOOL] ◀   结果: {self._render_payload(frame.result)}\n"
                # 只如实报告框架字段，不解释成「有没有触发下一轮生成」：
                # 实测该字段与实际行为并不一致（值为 False 时下一轮照样发生），
                # 把它翻译成结论会写出**误导性**日志 —— 那比不写更糟。
                # 想知道后续有没有再生成一轮，看后面那条完整上下文日志即可。
                f"[TOOL] ◀   框架字段 run_llm={frame.run_llm!r}"
                + (f"\n[TOOL] ◀   错误: {frame.error}" if frame.error else "")
            )

        elif isinstance(frame, FunctionCallCancelFrame):
            if self._is_duplicate_broadcast(frame):
                return
            self._tool_t0.pop(frame.tool_call_id, None)
            logger.warning(
                f"[TOOL] ✖ {frame.function_name} 被取消"
                f"（id={frame.tool_call_id}）—— 通常是用户中途打断"
            )

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

    def _is_duplicate_broadcast(self, frame) -> bool:
        """广播帧的第二次投递返回 True，调用方据此跳过。

        为什么需要（这是框架行为，不是本项目的 bug）：
            pipecat 的 ``broadcast_frame`` 会为上行、下行**各创建一个 Frame 实例**
            （见 ``processors/frame_processor.py``），两个实例有各自独立的 ``id``，
            再用 ``broadcast_sibling_id`` 互相指向。
            而观察者的去重是按 ``frame.id`` 判定的
            （``pipeline/worker_observer.py``: ``data.first_push = frame.id not in ...``），
            两个实例都会被评为「首次推送」，于是观察者被通知两次 ——
            日志里每条广播帧就出现两遍。

        框架提供 ``broadcast_sibling_id`` 正是为了这种合并：
        首次见到时把**两个 id 都记下**，第二个实例到来时即命中。

        有界实现：只保留最近 512 个 id。上行/下行两个实例是紧挨着投递的，
        不需要长期记忆，因此不会像早期版本那样随会话无限增长。
        """
        sibling = getattr(frame, "broadcast_sibling_id", None)
        if sibling is None:
            return False  # 非广播帧，不参与合并

        if frame.id in self._seen_broadcast:
            return True

        for fid in (frame.id, sibling):
            if fid in self._seen_broadcast:
                continue
            if len(self._seen_order) == self._seen_order.maxlen:
                # append 会自动挤掉最旧的一个，同步从集合里移除，避免集合无限增长
                self._seen_broadcast.discard(self._seen_order[0])
            self._seen_broadcast.add(fid)
            self._seen_order.append(fid)
        return False

    @staticmethod
    def _render_payload(payload) -> str:
        """把工具参数/结果渲染成完整字符串。**不截断**。

        JSON 化而不是直接 str()：既保留嵌套结构，也避免 dict 的
        单引号写法在日志里难以复制复用。``default=str`` 兜住不可序列化的值。
        """
        try:
            return json.dumps(payload, ensure_ascii=False, default=str, indent=2)
        except (TypeError, ValueError):
            return repr(payload)

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
