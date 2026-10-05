"""Probe: does the brain interface actually work, and what does it cost?

Verifies the three things that would otherwise be discovered in production:

    1. streaming works and the first chunk arrives (the latency that decides "when does the
       bot start talking")
    2. the conversation handle is carried across turns (otherwise the platform forgets everything)
    3. a failure is reported, not swallowed (the phase-2 lesson: a silent tool is worse than
       an error)

Uses the stand-in brain, so it needs no platform and no network. That is deliberate: this
probe answers "is my adapter correct", not "is my platform good".

    uv run python probe/verify_brain.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from agent_client import BrainClient, BrainError
from settings import load_config
from stub_agent import INTERNAL_CODE, serve

PASS, FAIL = "[OK]", "[FAIL]"


async def check_streaming(base_url: str, cfg) -> tuple[bool, str]:
    brain = dataclasses.replace(cfg.brain, base_url=base_url, api_key="app-stub-key", user="probe")
    async with BrainClient(brain) as client:
        started = time.perf_counter()
        first_chunk_ms: float | None = None
        text = ""
        async for delta in client.stream("你的内部代号是什么"):
            if first_chunk_ms is None:
                first_chunk_ms = (time.perf_counter() - started) * 1000
            text += delta
        turn = client.last_turn

    ok = INTERNAL_CODE in text and first_chunk_ms is not None and bool(turn.conversation_id)
    detail = (
        f"first chunk {first_chunk_ms:.0f}ms, {len(turn.chunks)} chunks, "
        f"conversation_id={'set' if turn.conversation_id else 'MISSING'}, "
        f"answer={text[:40]!r}"
    )
    return ok, detail


async def check_conversation_carries(base_url: str, cfg) -> tuple[bool, str]:
    """Second turn must reuse the conversation id the first turn returned."""
    brain = dataclasses.replace(cfg.brain, base_url=base_url, api_key="app-stub-key", user="probe")
    async with BrainClient(brain) as client:
        async for _ in client.stream("第一句"):
            pass
        first_id = client.last_turn.conversation_id
        async for _ in client.stream("第二句", conversation_id=first_id):
            pass
        second_id = client.last_turn.conversation_id
    ok = bool(first_id) and first_id == second_id
    return ok, f"conversation_id kept: {first_id == second_id} ({str(first_id)[:8]}...)"


async def check_failure_is_loud(cfg) -> tuple[bool, str]:
    """An unreachable brain must raise with something sayable -- never look like an empty answer."""
    brain = dataclasses.replace(
        cfg.brain,
        base_url="http://127.0.0.1:1/v1",  # nothing listens here
        api_key="app-stub-key",
        user="probe",
    )
    async with BrainClient(brain) as client:
        try:
            async for _ in client.stream("在吗"):
                pass
        except BrainError as exc:
            return bool(exc.spoken), f"raised BrainError, spoken={exc.spoken!r}"
    return False, "no error raised for an unreachable brain"


async def check_sentence_splitting() -> tuple[bool, str]:
    """The adapter must hand TTS the first sentence early, not the whole answer."""
    from brain import BrainProcessor

    take = BrainProcessor._take_sentences
    got = take("第一句。第二句还没说完", last=False)
    ok = got == ["第一句。"]
    rest_ok = take("没有标点的长尾巴", last=True) == ["没有标点的长尾巴"]
    return ok and rest_ok, f"first={got} tail_ok={rest_ok}"


async def main() -> int:
    cfg = load_config()
    base_url, shutdown = serve(port=8799, first_chunk_delay=0.25, chunk_delay=0.01)
    print("=" * 72)
    print("voice-module / brain interface probe (stand-in brain, no platform, no network)")
    print("=" * 72)
    results: list[tuple[str, bool, str]] = []
    try:
        for name, coro in (
            ("streaming + first chunk + conversation id", check_streaming(base_url, cfg)),
            ("conversation id carried to the next turn", check_conversation_carries(base_url, cfg)),
            ("unreachable brain raises a sayable error", check_failure_is_loud(cfg)),
            ("first sentence handed over early", check_sentence_splitting()),
        ):
            ok, detail = await coro
            results.append((name, ok, detail))
            print(f"  {PASS if ok else FAIL} {name}")
            print(f"        {detail}")
    finally:
        shutdown()

    failed = [name for name, ok, _ in results if not ok]
    print("-" * 72)
    print(
        f"verdict: {PASS} brain interface works"
        if not failed
        else f"verdict: {FAIL} {len(failed)} check(s) failed: {failed}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
