"""Timeline for one spoken turn -- the only way to know where the milliseconds went.

A voice module has three latency budgets that must stay separable:

    heard      : user stopped talking -> we have the transcript      (VAD + STT, local)
    first word : we sent the request  -> first chunk back from the platform (network + its thinking)
    speaking   : first chunk          -> first audio out            (TTS, local)

If you cannot see these three apart, you will optimise the wrong one. Written from
scratch for phase 3; it deliberately records only what this module owns.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class TurnTimeline:
    """Marks for a single user turn, relative to the moment the user stopped talking."""

    t0: float = field(default_factory=time.perf_counter)
    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str) -> float:
        now = time.perf_counter()
        self.marks[name] = now
        return now

    def ms(self, name: str) -> float | None:
        at = self.marks.get(name)
        return None if at is None else (at - self.t0) * 1000.0

    def report(self) -> str:
        labels = [
            ("transcript", "heard"),
            ("brain_request", "request sent"),
            ("brain_first_chunk", "platform's first word"),
            ("first_sentence", "first full sentence"),
            ("tts_queued", "handed to TTS"),
        ]
        parts = []
        for key, label in labels:
            value = self.ms(key)
            if value is not None:
                parts.append(f"{label} {value:.0f}ms")
        return " | ".join(parts) if parts else "(no marks)"


def setup_logging(logs_dir: Path, session_id: str) -> Path:
    """One log file per session; console output stays readable (two lines per turn)."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"voice-module-{session_id}.log"
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        level="INFO",
        format="{time:HH:mm:ss} | {level: <7} | {message}",
    )
    logger.add(log_path, level="DEBUG", encoding="utf-8")
    logger.info(f"[BOOT] log file: {log_path}")
    return log_path
