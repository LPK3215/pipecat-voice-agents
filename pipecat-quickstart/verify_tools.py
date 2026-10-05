"""Verify function calling over a real LLM link (no browser, no microphone).

Backend capability only, without audio:
    text question -> LLM -> tool triggered -> tool result fed back -> LLM composes the final answer

Why a separate script:
    ``verify_stack.py`` runs the full voice path (STT->LLM->TTS), where whether a tool was
    actually called can only be inferred from synthesized speech. Here
    ``FunctionCallResultFrame`` is inspected directly, so "called / not called / what
    arguments / what result" is visible at a glance.

Why ``--repeat``:
    Whether the model calls a tool is **non-deterministic** (different every time at the
    default sampling temperature). **A comparison with a sample size of 1 is noise.** Talking
    about a "success rate" requires repeating N times and looking at the distribution --
    which this script builds in.

Four mutually exclusive outcomes (completely different debugging directions, never conflate):
    [OK]  tool called      normal
    [ERR] call failed      API error (no quota / rate limit / network) -> check the **environment**
    [ERR] no answer        request failed or pipeline stuck            -> check environment/pipeline
    [WARN] self-answered   model decided no tool was needed            -> check **model/prompt**

Usage:
    cd server && uv run ../verify_tools.py
    cd server && uv run ../verify_tools.py --question "今天星期几"
    cd server && uv run ../verify_tools.py --repeat 20        # measure the real success rate
    cd server && uv run ../verify_tools.py --dump-context     # print the context for debugging

Exit code 0 = at least one round produced a final answer.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "server"))

from dotenv import load_dotenv  # noqa: E402

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / "server" / ".env", override=True)

from loguru import logger  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    ErrorFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    LLMTextFrame,
    StartFrame,
)
from pipecat.observers.error_observer import ErrorObserver  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.processors.aggregators.llm_response_universal import (  # noqa: E402
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402
from pipecat.services.openai.llm import OpenAILLMService  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

import memory  # noqa: E402 - 需先 load_dotenv
from pipeline_logging import setup_logging  # noqa: E402
from settings import (  # noqa: E402
    DEFAULT_SYSTEM_INSTRUCTION,
    build_llm_extra,
    llm_config,
    thinking_disabled,
)
from tools import build_tools  # noqa: E402


class Kickoff(FrameProcessor):
    """Hand the tool-equipped context to the LLM after the pipeline starts and trigger one inference."""

    def __init__(self, context: LLMContext, run_frame: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._context = context
        self._run_frame = run_frame
        self._fired = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame) and not self._fired:
            self._fired = True
            # Let StartFrame flow downstream first (services initialise on it), then send the context.
            await self.push_frame(frame, direction)
            await self.push_frame(LLMContextFrame(context=self._context))
            if self._run_frame:
                await self.push_frame(LLMRunFrame())
            return
        await self.push_frame(frame, direction)


class Sink(FrameProcessor):
    """Collect the final text.

    Note: ``FunctionCallResultFrame`` is consumed by the assistant aggregator and is **not**
    forwarded downstream -- waiting for it here never succeeds (this once caused a false
    "timeout"). Tool usage is instead detected via the LLM service's
    ``on_function_calls_started`` event.

    Note 2: the model may speak a **preamble** ("let me check") *before* requesting a tool.
    That text is not the answer, and an earlier version accepted it as the final answer the
    moment a response ended -- so rounds were silently scored on a preamble. A response is
    therefore only accepted here after a short settle window in which **no** tool request
    follows it (see ``_settle``).
    """

    SETTLE_SECS = 1.0

    def __init__(self, tool_calls: list[str], **kwargs):
        super().__init__(**kwargs)
        self.text: list[str] = []
        self.errors: list[str] = []
        self.done = asyncio.Event()
        self._tool_calls = tool_calls
        self._current: list[str] = []
        self._calls_at_start = 0

    async def _settle(self, candidate: str, calls_at_end: int) -> None:
        """Accept ``candidate`` only if no tool request shows up in the settle window."""
        await asyncio.sleep(self.SETTLE_SECS)
        if len(self._tool_calls) == calls_at_end:
            self.text = [candidate]
            self.done.set()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        logger.debug(f"[FRAME] sink received {type(frame).__name__}")

        if isinstance(frame, LLMFullResponseStartFrame):
            self._current = []
            self._calls_at_start = len(self._tool_calls)
        elif isinstance(frame, LLMTextFrame):
            self._current.append(frame.text)
        elif isinstance(frame, ErrorFrame):
            # Must be captured: otherwise "call failed" is misreported as "the model answered
            # on its own". A 429 (no quota) was once read as "the model did not call the tool" --
            # a completely wrong conclusion that looked entirely normal.
            self.errors.append(str(getattr(frame, "error", frame)))
        elif isinstance(frame, LLMFullResponseEndFrame):
            # A tool request that landed *during* this response makes the text a preamble
            # ("let me check"), so discard it outright; the answer comes after the tool result.
            if self._current and len(self._tool_calls) == self._calls_at_start:
                self.create_task(
                    self._settle("".join(self._current), len(self._tool_calls)),
                    name="sink-settle",
                )

        await self.push_frame(frame, direction)


def classify(tool_calls: list[str], answer: str, errors: list[str]) -> str:
    """Classify one round into exactly one of four **mutually exclusive** kinds."""
    if tool_calls:
        return "tool_called"
    if errors:
        return "failed"
    if not answer:
        return "no_answer"
    return "answered_no_tool"


KIND_LABEL = {
    "tool_called": "[OK]   tool called",
    "failed": "[ERR]  call failed (API error)",
    "no_answer": "[ERR]  no answer (request failed / pipeline stuck)",
    "answered_no_tool": "[WARN] self-answered (no tool call)",
}


async def run_once(
    question: str,
    llm_cfg: dict,
    *,
    model: str,
    disable_thinking: bool,
    no_user_agg: bool = False,
    run_frame: bool = False,
    dump_context: bool = False,
    show: bool = True,
) -> dict:
    """Run one Q&A round; returns {kind, tool_calls, answer, errors}."""
    llm_kwargs: dict = {"model": model, "system_instruction": DEFAULT_SYSTEM_INSTRUCTION}
    extra = build_llm_extra(
        thinking_body=(llm_cfg["thinking_body"] if disable_thinking else None)
    )
    if extra:
        llm_kwargs["extra"] = extra
    llm = OpenAILLMService(
        api_key=llm_cfg["api_key"],
        base_url=llm_cfg["base_url"],
        settings=OpenAILLMService.Settings(**llm_kwargs),
    )

    # Tool calls can only be observed reliably here: FunctionCallResultFrame does not reach
    # the downstream sink.
    tool_calls: list[str] = []

    @llm.event_handler("on_function_calls_started")
    async def _on_function_calls(_service, function_calls):
        tool_calls.extend(fc.function_name for fc in function_calls)

    context = LLMContext(tools=build_tools("verify-tools"))
    context.add_message({"role": "user", "content": question})

    # The aggregators are required: after the tool result returns, the assistant aggregator
    # triggers the second LLM inference. Without it the tool is called correctly but the
    # pipeline then idles and the final answer never arrives.
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams()
    )

    # NOTE: order matters -- Sink must be placed **before** the assistant aggregator.
    # The aggregator digests LLMTextFrame into LLMContextFrame / *TurnFrame context frames and
    # does not forward text frames downstream -- placed after it, no text is ever received
    # (this once looked like a hang).
    sink = Sink(tool_calls)
    if no_user_agg:
        stages = [Kickoff(context, run_frame=run_frame), llm, sink, assistant_agg]
    else:
        stages = [
            Kickoff(context, run_frame=run_frame),
            user_agg,
            llm,
            sink,
            assistant_agg,
        ]

    # Errors must be collected via ErrorObserver, not by hoping Sink receives an ErrorFrame:
    # an ErrorFrame produced by an LLM error is handled at the worker layer and does not travel
    # to the end of the pipeline, so that branch in Sink usually catches nothing. A 429 (no
    # quota) was once read as "the model did not call the tool" because of this.
    err_observer = ErrorObserver()
    errors: list[str] = []

    @err_observer.event_handler("on_error")
    async def _on_error(_observer, event):
        errors.append(f"{event.category.value} | {event.message}")

    worker = PipelineWorker(
        Pipeline(stages),
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[err_observer],
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    run_task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(sink.done.wait(), timeout=90)
    except TimeoutError:
        logger.error("[VERIFY] timed out after 90s; no 'tool call + final answer'")
    finally:
        await runner.cancel()
        try:
            await asyncio.wait_for(run_task, timeout=30)
        except (TimeoutError, asyncio.CancelledError):
            logger.warning("[VERIFY] pipeline shutdown timed out; forcing exit")

    answer = "".join(sink.text).strip()
    all_errors = errors + sink.errors
    kind = classify(tool_calls, answer, all_errors)

    if show:
        print("-" * 74)
        if dump_context:
            print("  messages in the context (for 'model spoke but no text frame'):")
            for msg in context.messages:
                role = msg.get("role") if isinstance(msg, dict) else "?"
                content = msg.get("content") if isinstance(msg, dict) else ""
                print(f"    [{role}] {str(content)[:200]!r}")
        print(f"  {KIND_LABEL[kind]}" + (f": {', '.join(tool_calls)}" if tool_calls else ""))
        print(f"  final answer: {answer!r}")
        for err in all_errors:
            print(f"  error: {err}")

    return {"kind": kind, "tool_calls": tool_calls, "answer": answer, "errors": all_errors}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", default="现在几点了？", help="what to ask (should trigger a tool)")
    ap.add_argument("--repeat", type=int, default=1, help="repeat N times and compute a success rate (>=5 recommended)")
    ap.add_argument("--dump-context", action="store_true", help="print context messages to debug missing text output")
    ap.add_argument("--model", default=None, help="override the current provider's model name")
    ap.add_argument("--think", action="store_true", help="enable thinking mode (overrides the .env setting)")
    ap.add_argument("--run-frame", action="store_true", help="also push an LLMRunFrame (for comparison)")
    ap.add_argument("--no-user-agg", action="store_true", help="put only the assistant aggregator in the pipeline")
    args = ap.parse_args()

    llm_cfg = llm_config()
    model = args.model or llm_cfg["model"]
    if not llm_cfg["api_key"]:
        print(f"missing {llm_cfg['api_key_env']} (it belongs in server/.env)")
        return 1

    # Same local stores as bot.py, otherwise DB-backed tools (remember_fact / recall_fact /
    # query_data) fail here with "no such table" and the self-check reports a false negative.
    memory.init_db()
    memory.seed_demo_business()

    repeat = max(1, args.repeat)
    run_log, _ = setup_logging(prefix="verify-tools")

    print("=" * 74)
    print("function calling self-check (same config as server/bot.py)")
    print(f"  LLM = {llm_cfg['provider']} | {model}")
    # Disabling thinking is spelled differently per provider; that is thinking_body's job
    # (--think forces thinking back on).
    disable_thinking = False if args.think else thinking_disabled()
    print(f"  disable_thinking = {disable_thinking}")
    print(f"  question = {args.question!r}    repeat = {repeat}")
    print(f"  log = {run_log}")
    print("=" * 74)

    results = []
    for i in range(repeat):
        if repeat > 1:
            print(f"\n>>> round {i + 1}/{repeat}")
        results.append(
            await run_once(
                args.question,
                llm_cfg,
                model=model,
                disable_thinking=disable_thinking,
                no_user_agg=args.no_user_agg,
                run_frame=args.run_frame,
                dump_context=args.dump_context,
                show=True,
            )
        )

    if repeat > 1:
        counts = {k: 0 for k in KIND_LABEL}
        for r in results:
            counts[r["kind"]] += 1
        print()
        print("=" * 74)
        print(f"summary ({repeat} rounds) -- a sample size >1 is what makes it a 'success rate'")
        print("=" * 74)
        for kind, label in KIND_LABEL.items():
            n = counts[kind]
            print(f"  {label:<34}{n:>3}/{repeat}  {n / repeat * 100:6.1f}%")
        called = counts["tool_called"]
        print("-" * 74)
        print(f"  -> tool call rate = {called / repeat * 100:.1f}% (the rest: not called / failed)")
        print("=" * 74)

    return 0 if any(r["answer"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
