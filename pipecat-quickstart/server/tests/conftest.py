"""Shared pytest fixtures.

These are **unit tests**: no network, no LLM calls, no speech models -- pure logic only
(config resolution, tool handlers, SQL whitelist and persistence). End-to-end verification is
still done by the repo-root scripts scripts/verify_stack.py / scripts/smoke.py / scripts/audio_probe.py.

Why they exist: the repo previously had no unit tests, so regression checks meant running the
full path (minutes). These pin down the logic that "a one-line change can silently break" --
notably single-character search_facts queries, the SQL whitelist, and the per-provider
"disable thinking" parameters.
"""

import pathlib
import sys

import pytest

# Let tests import modules under server/ (settings/tools/memory/...)
SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point memory's database at a temp file so the real data/memory.db is not polluted."""
    import memory

    monkeypatch.setattr(memory, "DB_PATH", tmp_path / "test.db")
    memory.init_db()
    return memory
