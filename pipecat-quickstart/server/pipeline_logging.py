"""Unified observability: frontend events, pipeline frames, LLM I/O, per-turn latency.

Design goal (one run is enough to locate a problem):
    A single run should tell you who said what, when, how long it took, and what failed.

Outputs two files:
    logs/<prefix>-<timestamp>.log   full history of this run
    logs/<prefix>-latest.log        fixed name, always points at the latest run

Log tag conventions:
    [BOOT]   startup config (model / STT / TTS / VAD / transport / key status)
    [CLIENT] frontend events (connect, disconnect, RTVI messages, client info)
    [TURN]   per-turn timeline and stage latencies
    [FRAME]  key frame trace (DEBUG level, for troubleshooting)
    [ERROR]  pipeline failures (processor / category / still usable?), also sent to frontend
    [TOOL]   full function-call record (requested / started / result / failed / timeout / cancelled)

Canonical way to log [TOOL] (read this before changing it):
    Always use pipecat's built-in ``FunctionCallObserver``; do NOT hand-write frame
    listeners. An earlier version of this project did that and hit three traps:
      1) missing outcomes: a hand-written version only knew ok/failed, while the framework
         distinguishes started / in_progress / completed / failed / timed_out / cancelled.
         timed_out and cancelled were lost -- and "called but never returned" is exactly
         the case you most need to see.
      2) duplicate broadcast frames: the four function-call frames are sent via
         ``broadcast_frame``, producing one upstream and one downstream instance with
         different ``frame.id``; both count as "first push", so each call is logged twice.
         The framework handles this by only reading the DOWNSTREAM instance
         (``frame.broadcast_sibling_id is not None and direction != DOWNSTREAM -> return``).
      3) timing had to be inferred externally: the framework writes ``started_at`` /
         ``in_progress_at`` into the event, so a single record yields both the queue wait
         and the execution time.
    NOTE: ``FunctionCallObserver`` has ``include_results=False`` by DEFAULT (arguments only).
    Pass ``True`` explicitly, otherwise you lose half the information.

Note: pipecat's runner calls ``logger.remove()`` on startup
(pipecat/runner/run.py), so ``setup_logging`` must be called again after the session starts.
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
# Log sinks
# ---------------------------------------------------------------------------
def resolve_log_paths(prefix: str = "bot") -> tuple[Path, Path]:
    """Compute log paths only (no sinks attached; paths are computed once and reused)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return LOG_DIR / f"{prefix}-{stamp}.log", LOG_DIR / f"{prefix}-latest.log"


def setup_logging(
    run_log: Path | None = None,
    latest: Path | None = None,
    prefix: str = "bot",
) -> tuple[Path, Path]:
    """Attach log sinks: console at INFO + two file sinks at DEBUG (everything).

    Returns:
        (this run's log file, the fixed-name latest log file)
    """
    if run_log is None or latest is None:
        run_log, latest = resolve_log_paths(prefix)

    logger.remove()
    logger.add(sys.stderr, level="INFO", format=CONSOLE_FORMAT, colorize=True)
    # Full DEBUG dump, including all pipecat internals.
    # Append mode for the per-run file: setup_logging is called more than once per
    # process, and "w" would wipe the [BOOT] lines written at the start of this run.
    logger.add(run_log, level="DEBUG", format=FILE_FORMAT, encoding="utf-8")
    # The latest file must be truncated: loguru appends by default, which would grow
    # forever and make "always points at the latest run" a lie.
    logger.add(latest, level="DEBUG", format=FILE_FORMAT, encoding="utf-8", mode="w")
    return run_log, latest


def log_boot_banner(config: dict) -> None:
    """Print the config that is actually in effect -- prevents drift-induced misreads."""
    logger.info("[BOOT] " + "=" * 60)
    logger.info("[BOOT] Effective configuration for this run")
    for key, value in config.items():
        logger.info(f"[BOOT]   {key:<16}= {value}")
    logger.info("[BOOT] " + "=" * 60)


# ---------------------------------------------------------------------------
# Frontend (browser) events
# ---------------------------------------------------------------------------
def install_client_logging(transport, rtvi) -> None:
    """Attach frontend-side event logging."""
    if rtvi is not None:

        @rtvi.event_handler("on_client_ready")
        async def _on_client_ready(_rtvi):
            logger.info("[CLIENT] frontend ready (RTVI on_client_ready)")

        @rtvi.event_handler("on_client_message")
        async def _on_client_message(_rtvi, message):
            logger.info(f"[CLIENT] client message: {message}")

        @rtvi.event_handler("on_ui_message")
        async def _on_ui_message(_rtvi, message):
            logger.debug(f"[CLIENT] UI message: {message}")

        @rtvi.event_handler("on_bot_started")
        async def _on_bot_started(_rtvi):
            logger.info("[CLIENT] bot session started (on_bot_started)")

    @transport.event_handler("on_client_connected")
    async def _on_connected(_transport, client):
        logger.info(f"[CLIENT] browser connected | {_describe(client)}")

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_transport, client):
        logger.info(f"[CLIENT] browser disconnected | {_describe(client)}")


def _describe(client) -> str:
    """Print as much readable info as possible from the client object."""
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
# Failure reporting: log + make it visible in the frontend
# ---------------------------------------------------------------------------
# Translate pipecat error categories into plain language for humans and end users.
# The categories are vendor-independent, so this table still holds for other providers.
ERROR_HINTS: dict[ErrorCategory, str] = {
    ErrorCategory.AUTHENTICATION: "invalid or missing API key",
    ErrorCategory.AUTHORIZATION: "API key not authorized for this resource",
    ErrorCategory.INVALID_REQUEST: "request rejected by server (bad params or message format)",
    ErrorCategory.RATE_LIMIT: "rate limited, too many requests",
    ErrorCategory.QUOTA: "account quota exhausted",
    ErrorCategory.CONNECTIVITY: "cannot reach the service, check the network",
    ErrorCategory.SERVER: "internal server error",
    ErrorCategory.APPLICATION: "application code error",
    ErrorCategory.UNKNOWN: "unknown error",
}


def install_error_reporting(worker, observer: ErrorObserver | None = None) -> ErrorObserver:
    """Log pipeline failures and push them to the frontend.

    Why this exists:
        Without it, a service failure is silent in the browser -- the connection and the
        handshake both succeed, and only after the user speaks does it become obvious that
        nobody answers. That is easily misread as a network problem.
        pipecat's ``ErrorObserver`` catches the error at its **source** (instead of waiting
        for it to travel to the end of the pipeline, where another processor may have
        already consumed it) and reports processor / category / exception type / usability.

    No frontend changes needed: ``rtvi.send_error()`` emits an ``error`` message that is
    already part of the official prebuilt frontend's RTVI message set.

    Args:
        worker: the pipeline worker, used to reach ``worker.rtvi``.
        observer: reuse an existing observer; a new one is created if omitted.

    Returns:
        The ErrorObserver -- it only takes effect if added to
        ``PipelineWorker(observers=[...])``.
    """
    if observer is None:
        observer = ErrorObserver()

    # Push each (processor, category) at most once: otherwise a rate limit or a dropped
    # network connection floods the frontend.
    sent: set[tuple[str, str]] = set()

    @observer.event_handler("on_error")
    async def _on_error(_observer, event):
        hint = ERROR_HINTS.get(event.category, ERROR_HINTS[ErrorCategory.UNKNOWN])
        scope = "service unusable" if not event.processor_usable else "single failure"
        logger.error(
            f"[ERROR] {event.processor} | {event.category.value} | {scope} | "
            f"exception={event.exception_type or '-'} | {event.message}"
        )

        rtvi = getattr(worker, "rtvi", None)
        if rtvi is None:
            return
        key = (event.processor, event.category.value)
        if key in sent:
            return
        sent.add(key)
        try:
            await rtvi.send_error(f"{hint} ({event.processor})")
            logger.info(f"[ERROR] pushed to frontend: {hint} ({event.processor})")
        except Exception as exc:  # noqa: BLE001 - failing to notify must not break the run
            logger.warning(f"[ERROR] failed to push to frontend: {exc}")

    return observer


# ---------------------------------------------------------------------------
# Function call logging (the way the framework recommends)
# ---------------------------------------------------------------------------

# The framework splits a function call into six outcomes. The first two are "start",
# the other four are "outcomes"; everything except completed is an error path, and
# timed_out / cancelled are the usual cause of "called but no result", so they must be
# visible at a glance. Keep these ASCII-only: they end up in terminals.
_FUNCTION_CALL_LABELS = {
    FunctionCallEventKind.STARTED: "[>>] model requested",
    FunctionCallEventKind.IN_PROGRESS: "[> ] executing",
    FunctionCallEventKind.COMPLETED: "[OK] completed",
    FunctionCallEventKind.FAILED: "[ERR] failed",
    FunctionCallEventKind.TIMED_OUT: "[TMO] timed out",
    FunctionCallEventKind.CANCELLED: "[CAN] cancelled",
}


def _render_payload(payload) -> str:
    """Render arguments/result as a full string. **No truncation.**

    Use JSON instead of str(): nested structure is preserved and dict single-quote
    output is avoided (hard to copy/paste from logs). ``default=str`` covers
    non-serializable values.
    """
    try:
        return json.dumps(payload, ensure_ascii=False, default=str, indent=2)
    except (TypeError, ValueError):
        return repr(payload)


def install_function_call_logging(
    observer: FunctionCallObserver | None = None,
) -> FunctionCallObserver:
    """Log every function call using the framework's own ``FunctionCallObserver``.

    Why not hand-write it (traps this project already hit; see module docstring):
        A hand-written version only knew ok/failed (losing timeout and cancel), had to
        dedupe broadcast frames itself, and had to infer timings from frames.

    NOTE: ``include_results=True`` must be passed explicitly -- its default is ``False``
    (arguments only), which costs you half the information when debugging.

    Args:
        observer: reuse an existing observer; a new one is created if omitted.

    Returns:
        The observer instance -- it only takes effect if added to
        ``PipelineWorker(observers=[...])``.
    """
    if observer is None:
        observer = FunctionCallObserver(include_results=True)

    @observer.event_handler("on_function_call_event")
    async def _on_function_call_event(_observer, event):
        label = _FUNCTION_CALL_LABELS.get(event.kind, event.kind.value)
        lines = [f"[TOOL] {label} {event.function_name} (id={event.tool_call_id})"]

        # Queue and execution times are written into the event by the framework.
        now = getattr(event, "timestamp", None)
        if event.started_at and event.in_progress_at and event.in_progress_at >= event.started_at:
            lines.append(
                f"[TOOL]     queue wait {(event.in_progress_at - event.started_at) * 1000:.0f}ms"
            )
        if now and event.in_progress_at and now >= event.in_progress_at:
            lines.append(f"[TOOL]     exec time {(now - event.in_progress_at) * 1000:.0f}ms")

        if event.arguments is not None:
            lines.append(f"[TOOL]     arguments: {_render_payload(event.arguments)}")
        if event.result is not None:
            lines.append(f"[TOOL]     result: {_render_payload(event.result)}")
        if event.error is not None:
            lines.append(f"[TOOL]     error: {event.error}")
        if event.blocking is not None:
            lines.append(
                "[TOOL]     blocking: "
                f"{'yes, waits for result' if event.blocking else 'no, filled in later'}"
            )

        text = "\n".join(lines)
        # Error outcomes use warning level so they can be filtered out when asking
        # "why was there no answer".
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
# Conversation timeline observer
# ---------------------------------------------------------------------------
class ConversationLogger(BaseObserver):
    """Turn pipeline frames into a readable conversation timeline with per-turn latencies.

    Dedup is delegated to the framework: pipecat's ``BaseObserver`` supports
    ``observe_every_push=False``, which notifies only on a frame's **first** push.

    An earlier version deduped with its own ``frame.id`` set; that is no longer needed:
      - the hand-written set grew forever and needed "clear it all when it gets big",
        which then re-classified still-in-flight frames as unseen and duplicated logs;
      - the framework dedupes by push count and has neither problem.

    Log line format is a cross-file contract: ``text_probe.py`` / ``audio_probe.py`` /
    ``live_asr_bench.py`` parse it with regexes. If you change a message, update those.
    """

    def __init__(self, **kwargs):
        # Only observe the first push: otherwise a frame passing N processors is logged N times.
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
            logger.debug("[FRAME] VAD: user started speaking")

        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._mark("end_of_speech", now)
            logger.info("[TURN] user stopped speaking (VAD end of turn)")

        elif isinstance(frame, TranscriptionFrame):
            self._mark("transcript", now)
            logger.info(f"[TURN] transcript: {frame.text!r}")

        elif isinstance(frame, LLMContextFrame):
            self._mark("to_llm", now)
            logger.info(f"[TURN] -> context sent to LLM: {self._render_context(frame)}")

        elif isinstance(frame, LLMFullResponseStartFrame):
            self._reply = []
            logger.debug("[FRAME] LLM started responding")

        elif isinstance(frame, LLMTextFrame):
            self._mark("first_token", now)
            self._reply.append(getattr(frame, "text", "") or "")

        elif isinstance(frame, LLMFullResponseEndFrame):
            logger.info(f"[TURN] <- LLM response complete: {''.join(self._reply)!r}")

        elif isinstance(frame, TTSAudioRawFrame):
            if "first_audio" not in self._marks:
                self._mark("first_audio", now)
                logger.info("[TURN] TTS first audio")
                self._report_turn()

        # Function calls are not logged here -- that is the framework's
        # FunctionCallObserver (see install_function_call_logging).

        elif isinstance(frame, BotStartedSpeakingFrame):
            logger.debug("[FRAME] bot started speaking")

        elif isinstance(frame, BotStoppedSpeakingFrame):
            logger.debug("[FRAME] bot stopped speaking")

        elif isinstance(frame, ErrorFrame):
            logger.error(f"[FRAME] pipeline error: {frame.error}")

        elif isinstance(frame, UserStoppedSpeakingFrame):
            logger.debug("[FRAME] turn end marker UserStoppedSpeaking")

    # ---------------- internals ----------------
    def _start_turn(self, now: float) -> None:
        self._turn_t0 = now
        self._marks = {}
        self._reply = []
        self._turns += 1
        logger.info(f"[TURN] ----- turn {self._turns} started -----")

    def _mark(self, name: str, now: float) -> None:
        if self._turn_t0 is None or name in self._marks:
            return
        self._marks[name] = now

    @staticmethod
    def _render_context(frame) -> str:
        """Fully render the context sent to the LLM. **No truncation.**

        An earlier version kept only the last 600 characters, which cut off the system
        prompt and the injected long-term memory -- exactly the two parts you need when
        asking "why did the model answer like that". Now every message is listed in full,
        together with the tools available this turn: when the model picks the wrong tool,
        the first thing to check is which tools it could actually see.
        """
        context = getattr(frame, "context", None)
        messages = getattr(context, "messages", None) or context
        try:
            items = list(messages)
        except TypeError:
            return repr(frame)

        lines = [f"({len(items)} messages)"]
        for i, msg in enumerate(items, 1):
            if isinstance(msg, dict):
                role, content = msg.get("role", "?"), msg.get("content", "")
            else:
                role = getattr(msg, "role", "?")
                content = getattr(msg, "content", msg)
            # Assistant messages carrying tool_calls must be shown too, otherwise the
            # "what did the model call" part is empty.
            calls = msg.get("tool_calls") if isinstance(msg, dict) else None
            suffix = f"  tool_calls={json.dumps(calls, ensure_ascii=False, default=str)}" if calls else ""
            lines.append(f"  {i}. [{role}] {content}{suffix}")

        try:
            tools = getattr(context, "tools", None)
            names = [t.name for t in getattr(tools, "standard_tools", [])]
        except Exception:  # noqa: BLE001 - tool list is extra info; failure must not matter
            names = []
        lines.append(f"  tools available this turn ({len(names)}): {names}")
        return "\n".join(lines)

    def _report_turn(self) -> None:
        base = self._marks.get("end_of_speech")
        if base is None:
            return
        rows = []
        for name in ("transcript", "to_llm", "first_token", "first_audio"):
            ts = self._marks.get(name)
            rows.append(f"{name} {(ts - base) * 1000:.0f}ms" if ts else f"{name} N/A")
        logger.info("[TURN] latency (baseline=end_of_speech): " + " | ".join(rows))
