#!/usr/bin/env python3
"""Runtime check: does the framework's automatic context summarization actually fire?

Why a separate script:
    Summarization only triggers above a threshold (default: more than 20 unsummarized
    messages), so a normal short self-check never reaches it. This one pre-fills the
    context past the threshold, runs one real inference, and waits for the summarizer's
    ``on_summary_applied`` event.

    It exists because this capability was previously a hand-written observer; when it was
    replaced by the framework's ``LLMContextSummarizer`` (created inside the assistant
    aggregator), the swap had to be *measured*, not assumed -- silent failure is this
    project's most common failure mode.

Usage:
    cd server && uv run ../scripts/verify_summarize.py
    cd server && uv run ../scripts/verify_summarize.py --prefill 30

Exit code 0 = a compaction really happened.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Scripts live in scripts/; the project root (server/, sample-data/, docs/) is one level up.
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "server"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE / "server" / ".env", override=True)

from loguru import logger  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    Frame,
    LLMContextFrame,
    LLMRunFrame,
    LLMTextFrame,
    StartFrame,
)
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.processors.aggregators.llm_response_universal import (  # noqa: E402
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402
from pipecat.services.openai.llm import OpenAILLMService  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

from pipeline_logging import setup_logging  # noqa: E402
from settings import (  # noqa: E402
    DEFAULT_SYSTEM_INSTRUCTION,
    DEFAULT_SUMMARY_MAX_MESSAGES,
    build_llm_extra,
    build_summarization_config,
    llm_config,
)

DEFAULT_PREFILL = DEFAULT_SUMMARY_MAX_MESSAGES + 4

# NOTE: the filler text is intentionally Chinese -- it stands in for a Chinese conversation.
FILLER_USER = "第{i}条历史消息，用来把上下文撑过摘要阈值。"
FILLER_ASSISTANT = "收到。"


class Kickoff(FrameProcessor):
    """Hand the pre-filled context to the LLM once the pipeline starts."""

    def __init__(self, context: LLMContext, **kwargs):
        super().__init__(**kwargs)
        self._context = context
        self._fired = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame) and not self._fired:
            self._fired = True
            await self.push_frame(frame, direction)
            await self.push_frame(LLMContextFrame(context=self._context))
            await self.push_frame(LLMRunFrame())
            return
        await self.push_frame(frame, direction)


class Sink(FrameProcessor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.text: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self.text.append(frame.text)
        await self.push_frame(frame, direction)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefill", type=int, default=DEFAULT_PREFILL, help="message pairs to add")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for compaction")
    args = ap.parse_args()

    cfg = llm_config()
    if not cfg["api_key"]:
        print(f"missing {cfg['api_key_env']} (it belongs in server/.env)")
        return 1

    summary_cfg = build_summarization_config()
    print("=" * 74)
    print("context summarization self-check (framework LLMContextSummarizer)")
    print(f"  LLM = {cfg['provider']} | {cfg['model']}")
    print(
        f"  thresholds: max_unsummarized_messages={summary_cfg.max_unsummarized_messages} "
        f"max_context_tokens={summary_cfg.max_context_tokens}"
    )
    print(f"  prefill: {args.prefill} user/assistant pairs")
    print("=" * 74)

    llm_kwargs: dict = {"model": cfg["model"], "system_instruction": DEFAULT_SYSTEM_INSTRUCTION}
    extra = build_llm_extra(thinking_body=None)
    if extra:
        llm_kwargs["extra"] = extra
    llm = OpenAILLMService(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        settings=OpenAILLMService.Settings(**llm_kwargs),
    )

    context = LLMContext()
    for i in range(args.prefill):
        context.add_message({"role": "user", "content": FILLER_USER.format(i=i)})
        context.add_message({"role": "assistant", "content": FILLER_ASSISTANT})
    context.add_message({"role": "user", "content": "现在请用一句话回答：你好。"})
    before = len(context.messages)

    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(),
        assistant_params=LLMAssistantAggregatorParams(
            enable_auto_context_summarization=True,
            auto_context_summarization_config=summary_cfg,
        ),
    )

    summarizer = getattr(assistant_agg, "_summarizer", None)
    print(f"  summarizer created by the aggregator: {summarizer is not None}")
    if summarizer is None:
        print("verdict: [ERR] the assistant aggregator did not create a summarizer")
        return 1

    applied = asyncio.Event()

    @summarizer.event_handler("on_request_summarization")
    async def _on_request(_summarizer, frame):
        logger.info(f"[SUMMARY] requested: request_id={frame.request_id}")

    @summarizer.event_handler("on_summary_applied")
    async def _on_summary_applied(_summarizer, event):
        print(
            f"  summary applied: {event.original_message_count} -> {event.new_message_count} "
            f"messages (compressed {event.summarized_message_count}, "
            f"kept {event.preserved_message_count})"
        )
        applied.set()

    sink = Sink()
    worker = PipelineWorker(
        Pipeline([Kickoff(context), user_agg, llm, sink, assistant_agg]),
        params=PipelineParams(enable_metrics=True),
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    run_task = asyncio.create_task(runner.run())
    fired = False
    try:
        await asyncio.wait_for(applied.wait(), timeout=args.timeout)
        fired = True
    except TimeoutError:
        logger.error(f"[SUMMARY] no compaction within {args.timeout:.0f}s")
    finally:
        await runner.cancel()
        try:
            await asyncio.wait_for(run_task, timeout=30)
        except (TimeoutError, asyncio.CancelledError):
            logger.warning("[SUMMARY] pipeline shutdown timed out; forcing exit")

    after = len(context.messages)
    print("-" * 74)
    print(f"  messages: {before} before -> {after} after")
    print(f"  reply: {''.join(sink.text)[:80]!r}")
    ok = fired and after < before
    print("verdict:", "[OK] auto summarization fired" if ok else "[ERR] no compaction happened")
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    setup_logging(prefix="verify-summarize")
    sys.exit(asyncio.run(main()))
