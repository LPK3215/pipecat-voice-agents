"""Explicit orchestration: hard-code multi-step tool chains instead of hoping the
model links the steps itself.

Background (measured in HANDBOOK-02 sections 4/5):
    The model **skips intermediate steps**. Asked "what is the weather here", it does not
    first ``recall_fact`` to get the city; it invents "Beijing" and looks up the weather
    there -- the tool really was called and the answer format is fine, **the content is
    wrong**. That is the most insidious class of error because it looks completely normal.

Approach: define a "**composite tool**" = an ordered list of steps where a later step's
arguments can be taken from earlier results. The model calls just this one tool and the
**ordering is guaranteed by code**.

    STEPS = [(recall_fact, {"query": "city"}), (get_weather, city_from_previous)]

Relation to the framework:
    pipecat ships ``flows/`` (``FlowManager``), a conversation state machine for
    "**staged business dialogs**" (order flow: pick item -> order -> pay). This module
    solves a narrower but much more common problem -- **a fixed tool-call chain**. The two
    coexist: use this module inside a stage, FlowManager across stages.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# A step: handler + either an argument dict or a function that builds args from prior results.
ArgsFn = Callable[[list[dict]], dict]
Step = tuple[Callable, "dict | ArgsFn"]


class _Collector:
    """Minimal FunctionCallParams stand-in so tool handlers can be reused inside a chain."""

    def __init__(self, arguments: dict) -> None:
        self.arguments = arguments or {}
        self.result: dict | None = None

    async def result_callback(self, result, properties=None):  # noqa: ANN001
        self.result = result


async def call_handler(handler, arguments: dict | None = None) -> dict:
    """Call a tool handler directly and get its result (reused inside a chain)."""
    params = _Collector(arguments or {})
    await handler(params)
    return params.result or {}


async def run_steps(steps: list[Step]) -> list[dict]:
    """Run the steps in order; each step can build its arguments from **all** prior results.

    If ``args`` is a dict it is used as-is; if it is a callable it is called with
    ``prev_results``.
    """
    results: list[dict] = []
    for handler, args in steps:
        arguments = args(results) if callable(args) else (args or {})
        results.append(await call_handler(handler, arguments))
    return results


def first_value(container: Any, key: str) -> Any:
    """Robustly pull a field out of a value that may be dict/list/str (chain helper)."""
    if isinstance(container, dict):
        return container.get(key)
    if isinstance(container, list):
        for item in container:
            v = first_value(item, key)
            if v:
                return v
    return None
