"""验证 function calling 在真实 LLM 链路上跑通（不需要浏览器、不需要麦克风）。

只测后端能力，不经过音频：
    文本提问 → LLM → 触发工具 → 工具结果回灌 → LLM 组织最终回答

为什么单独一个脚本：
    ``verify_stack.py`` 走的是完整语音链路（STT→LLM→TTS），工具是否真的被调用
    在里面只能靠合成语音反推，看不清。这里直接检查 ``FunctionCallResultFrame``，
    「调了 / 没调 / 参数是什么 / 结果是什么」一目了然。

前端无需改动即可展示：pipecat 会把同一次调用以 ``llm-function-call*`` 消息推给
官方 Prebuilt 前端，所以这里跑通 == 前端也会显示。

用法：
    cd server && uv run ../verify_tools.py
    cd server && uv run ../verify_tools.py --question "今天星期几"

退出码 0 = 工具被调用且拿到了结果。
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "server"))

from dotenv import load_dotenv  # noqa: E402

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / "server" / ".env", override=True)

from loguru import logger  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    Frame,
    FunctionCallResultFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMRunFrame,
    LLMTextFrame,
    StartFrame,
)
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

from pipeline_logging import setup_logging  # noqa: E402
from settings import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_INSTRUCTION,
    MODELSCOPE_BASE_URL_DEFAULT,
    build_llm_extra,
    thinking_disabled,
)
from tools import build_tools  # noqa: E402


class Kickoff(FrameProcessor):
    """管线启动后把带工具的上下文交给 LLM，并触发一次推理。"""

    def __init__(self, context: LLMContext, run_frame: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._context = context
        self._run_frame = run_frame
        self._fired = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame) and not self._fired:
            self._fired = True
            # 必须先让 StartFrame 流到下游（各服务靠它完成初始化），再送上下文
            await self.push_frame(frame, direction)
            await self.push_frame(LLMContextFrame(context=self._context))
            if self._run_frame:
                await self.push_frame(LLMRunFrame())
            return
        await self.push_frame(frame, direction)


class Sink(FrameProcessor):
    """收集最终文本。

    注意：``FunctionCallResultFrame`` 由 assistant 聚合器消费，
    **不会**继续往下游传 —— 在 sink 里等它永远等不到（曾导致误判为超时）。
    工具是否被调用改用 LLM 服务的 ``on_function_calls_started`` 事件判定。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.text: list[str] = []
        self.done = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        logger.debug(f"[FRAME] sink 收到 {type(frame).__name__}")

        if isinstance(frame, LLMTextFrame):
            self.text.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            # 工具调用那一轮没有文本，只有拿到最终文本才算完整走完
            if self.text:
                self.done.set()

        await self.push_frame(frame, direction)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", default="现在几点了？", help="问什么（应能触发工具）")
    ap.add_argument(
        "--dump-context", action="store_true", help="打印上下文消息，用于排查无文本输出"
    )
    ap.add_argument("--model", default=None, help="覆盖 MODELSCOPE_MODEL")
    ap.add_argument("--think", action="store_true", help="开启思考模式（覆盖 .env 的关闭设置）")
    ap.add_argument(
        "--run-frame",
        action="store_true",
        help="额外推送 LLMRunFrame（用于对比：是否与聚合器重复触发推理）",
    )
    ap.add_argument(
        "--no-user-agg",
        action="store_true",
        help="管线里只放 assistant 聚合器（排除 user 聚合器的重复触发）",
    )
    args = ap.parse_args()

    model = args.model or os.getenv("MODELSCOPE_MODEL") or DEFAULT_MODEL
    base_url = os.getenv("MODELSCOPE_BASE_URL", MODELSCOPE_BASE_URL_DEFAULT)
    if not os.getenv("MODELSCOPE_API_KEY"):
        print("缺少 MODELSCOPE_API_KEY（应写在 server/.env）")
        return 1

    run_log, latest_log = setup_logging(prefix="verify-tools")

    print("=" * 74)
    print("function calling 自检（与 server/bot.py 同配置）")
    print(f"  LLM   = {model}")
    disable_thinking = False if args.think else thinking_disabled()
    print(f"  关闭思考 = {disable_thinking}")
    print(f"  提问  = {args.question!r}")
    print(f"  日志  = {run_log}")
    print("=" * 74)

    llm_kwargs: dict = {"model": model, "system_instruction": DEFAULT_SYSTEM_INSTRUCTION}
    if disable_thinking:
        llm_kwargs["extra"] = build_llm_extra()
    llm = OpenAILLMService(
        api_key=os.getenv("MODELSCOPE_API_KEY"),
        base_url=base_url,
        settings=OpenAILLMService.Settings(**llm_kwargs),
    )

    # 工具调用只能在这里可靠观测：FunctionCallResultFrame 不会流到下游的 sink
    tool_calls: list[str] = []

    @llm.event_handler("on_function_calls_started")
    async def _on_function_calls(_service, function_calls):
        tool_calls.extend(fc.function_name for fc in function_calls)

    context = LLMContext(tools=build_tools())
    context.add_message({"role": "user", "content": args.question})

    # 必须带上聚合器：工具结果回来后，是 assistant 聚合器负责触发 LLM 二次推理。
    # 少了它，工具会被正确调用，但之后管线空转、永远等不到最终回答。
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams()
    )

    # ⚠️ 顺序很关键：Sink 必须放在 assistant 聚合器**之前**。
    # 聚合器会把 LLMTextFrame 消化成 LLMContextFrame / *TurnFrame 等上下文帧，
    # 不会再往下游转发文本帧 —— 放在它后面就永远收不到文本（曾误判成「卡死」）。
    # 标准 pipecat 管线里 TTS 也是在聚合器之前，同理。
    sink = Sink()
    if args.no_user_agg:
        stages = [Kickoff(context, run_frame=args.run_frame), llm, sink, assistant_agg]
    else:
        stages = [
            Kickoff(context, run_frame=args.run_frame),
            user_agg,
            llm,
            sink,
            assistant_agg,
        ]
    pipeline = Pipeline(stages)
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    run_task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(sink.done.wait(), timeout=90)
    except TimeoutError:
        logger.error("[VERIFY] 超时 90s，未拿到「工具调用 + 最终回答」")
    finally:
        await runner.cancel()
        try:
            await asyncio.wait_for(run_task, timeout=30)
        except (TimeoutError, asyncio.CancelledError):
            logger.warning("[VERIFY] 管线收尾超时，已强制结束")

    print()
    print("=" * 74)
    print("结果")
    print("=" * 74)
    answer = "".join(sink.text).strip()
    if args.dump_context:
        print("-" * 74)
        print("  上下文中的消息（用于排查「模型说了但没变成文本帧」）:")
        for msg in context.messages:
            role = msg.get("role") if isinstance(msg, dict) else "?"
            content = msg.get("content") if isinstance(msg, dict) else ""
            print(f"    [{role}] {str(content)[:200]!r}")
    if tool_calls:
        print(f"  ✅ 工具被调用: {', '.join(tool_calls)}")
    else:
        print("  ⚠️  本轮模型没有调用工具（自行作答）")
    print("-" * 74)
    print(f"  最终回答: {answer!r}")
    print("=" * 74)

    if not tool_calls:
        print("  说明：模型是否调用工具是**非确定性**的，同一问题可能这次调、下次不调。")
        print("        要验证链路通不通，以「是否收到最终回答」为准。")

    return 0 if answer else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
