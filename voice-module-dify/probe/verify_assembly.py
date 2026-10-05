"""Probe: do the parts actually assemble?

The server boots without a client, so "it started" does **not** prove the pipeline is
correct -- a wrong constructor argument only shows up when a client connects, which is
exactly when you least want to find out. This probe builds every piece by hand (services,
turn aggregation, the brain adapter, the pipeline, the worker) and reports what it got.

    uv run python probe/verify_assembly.py

It loads the local speech models on first run (whisper base + a piper voice), so the first
execution downloads a couple hundred MB.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from agent_client import BrainClient
from app import run_session  # noqa: F401  (import proves app.py is loadable)
from brain import BrainProcessor
from settings import build_stt, build_tts, build_vad, load_config

PASS, FAIL = "[OK]", "[FAIL]"


def main() -> int:
    cfg = load_config()
    print("=" * 72)
    print("voice-module / assembly probe (no client, no platform)")
    print("=" * 72)
    results: list[tuple[str, bool, str]] = []

    def record(name: str, fn):
        try:
            detail = fn()
            results.append((name, True, str(detail)))
            print(f"  {PASS} {name}")
            print(f"        {detail}")
        except Exception as exc:
            results.append((name, False, f"{type(exc).__name__}: {exc}"))
            print(f"  {FAIL} {name}")
            print(f"        {type(exc).__name__}: {exc}")

    record("config loads", lambda: f"brain={cfg.brain.base_url} stt={cfg.voice.stt_engine} "
                                  f"tts={cfg.voice.tts_engine} vad_stop={cfg.voice.vad_stop_secs}s")
    record("VAD builds", lambda: type(build_vad(cfg)).__name__)
    record("STT builds (loads the model once)", lambda: type(build_stt(cfg)).__name__)
    record("TTS builds", lambda: type(build_tts(cfg)).__name__)

    def build_aggregators() -> str:
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.processors.aggregators.llm_response_universal import (
            LLMContextAggregatorPair,
            LLMUserAggregatorParams,
        )

        context = LLMContext()
        user_agg, _assistant = LLMContextAggregatorPair(
            context, user_params=LLMUserAggregatorParams(vad_analyzer=build_vad(cfg))
        )
        return type(user_agg).__name__

    record("turn aggregation builds", build_aggregators)

    def build_brain() -> str:
        brain = BrainProcessor(BrainClient(cfg.brain), cfg.filler)
        assert callable(brain.on_user_turn_stopped) and callable(brain.on_user_turn_started)
        return f"{type(brain).__name__} with handlers wired"

    record("brain adapter builds", build_brain)

    def build_pipeline() -> str:
        from pipecat.frames.frames import Frame
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.worker import PipelineParams, PipelineWorker
        from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

        class Passthrough(FrameProcessor):
            async def process_frame(self, frame: Frame, direction: FrameDirection):
                await super().process_frame(frame, direction)
                await self.push_frame(frame, direction)

        pipeline = Pipeline(
            [
                Passthrough(name="in"),
                build_stt(cfg),
                Passthrough(name="turn"),
                BrainProcessor(BrainClient(cfg.brain), cfg.filler),
                build_tts(cfg),
                Passthrough(name="out"),
            ]
        )
        worker = PipelineWorker(pipeline, name="probe", params=PipelineParams(enable_metrics=True))
        return f"{type(pipeline).__name__} + {type(worker).__name__} built"

    record("pipeline + worker build", build_pipeline)

    failed = [name for name, ok, _ in results if not ok]
    print("-" * 72)
    if failed:
        print(f"verdict: {FAIL} {len(failed)} part(s) failed: {failed}")
    else:
        print(f"verdict: {PASS} every part assembles")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
