#!/usr/bin/env python3
"""Live-path ASR comparison: run the same sentences through different STT engines over a
full WebRTC path.

Why this is needed (the offline scripts/asr_bench.py is not enough):
    The offline bench feeds the WAV **directly to the model**, skipping the real path's
    Opus codec, WebRTC jitter buffer, and repeated resampling. Measured: a configuration
    that scores perfectly offline still mishears over the real path -- so only CER measured
    over the full path is trustworthy.

Approach:
    Spawn one bot.py (engine chosen via the STT_ENGINE environment variable), then for each
    test audio run scripts/audio_probe.py (--no-spawn reuses the same server) and collect the
    transcript and latency.

NOTE: scripts/audio_probe.py's output is a contract -- the regexes below parse its stdout. If you
change its printed labels, update them here too.

Usage:
    cd server && uv run ../scripts/live_asr_bench.py                      # only the default engine
    cd server && uv run ../scripts/live_asr_bench.py --engines sensevoice whisper
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Scripts live in scripts/; the project root (server/, sample-data/, docs/) is one level up.
BASE = Path(__file__).resolve().parent.parent
SERVER = BASE / "server"
PY = SERVER / ".venv" / "bin" / "python"

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/asr_bench.py sits next to this script
from asr_bench import CACHE, SENTENCES, synthesize  # noqa: E402


def wait_up(url: str, timeout: int = 180) -> bool:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2.0)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(1.0)
    return False


def spawn_bot(engine: str, port: int) -> subprocess.Popen:
    env = dict(os.environ, STT_ENGINE=engine)
    log = SERVER / "logs" / f"live-bench-{engine}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log, "wb")
    return subprocess.Popen(
        [str(PY), "bot.py", "--host", "127.0.0.1", "--port", str(port)],
        cwd=SERVER,
        env=env,
        stdout=fh,
        stderr=subprocess.STDOUT,
    )


def run_one(wav: Path, ref: str, port: int) -> dict:
    """Run one sentence and parse transcript, CER, and ASR latency from the probe output.

    audio_probe's server address comes from ``PROBE_PORT`` and must match the port the bot
    actually listens on, otherwise it connects to the default port (nothing there) and
    silently gets no results.
    """
    proc = subprocess.run(
        [
            str(PY),
            str(BASE / "scripts/audio_probe.py"),
            "--no-spawn",
            "--wait",
            "12",
            "--wav",
            str(wav),
            "--ref",
            ref,
        ],
        cwd=SERVER,
        env=dict(os.environ, PROBE_PORT=str(port)),
        capture_output=True,
        text=True,
        timeout=180,
    )
    out = proc.stdout
    if "audio not fully sent" in out or proc.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-6:])
        print(f"    [probe failed rc={proc.returncode}] {tail}")
        if proc.stderr.strip():
            print(f"    [stderr] {proc.stderr.strip().splitlines()[-1][:200]}")
    m_cer = re.search(r"CER\s*:\s*([\d.]+)%", out)
    m_lat = re.search(r"user transcript\s+(\d+)\s*ms", out)
    m_txt = re.search(r"transcript\s*:\s*(.*)", out)
    return {
        "text": m_txt.group(1).strip() if m_txt else "",
        "cer": float(m_cer.group(1)) / 100 if m_cer else None,
        "asr_ms": int(m_lat.group(1)) if m_lat else None,
    }


def bench_engine(engine: str, wavs: list[Path], port: int) -> dict:
    print(f"\n{'=' * 74}")
    print(f"engine: {engine}")
    print("=" * 74)
    proc = spawn_bot(engine, port)
    try:
        if not wait_up(f"http://127.0.0.1:{port}/"):
            print("  bot.py not ready; skipping this engine")
            return {}
        print("  server ready; testing sentence by sentence ...")

        rows = []
        for ref, wav in zip(SENTENCES, wavs):
            try:
                r = run_one(wav, ref, port)
            except subprocess.TimeoutExpired:
                print(f"  timeout: {ref}")
                continue
            rows.append((ref, r))
            cer_s = "N/A" if r["cer"] is None else f"{r['cer'] * 100:.1f}%"
            lat_s = "N/A" if r["asr_ms"] is None else f"{r['asr_ms']}ms"
            flag = "OK " if r["cer"] == 0 else "ERR"
            print(f"  [{flag}] {ref}")
            print(f"        transcript={r['text']}  (CER {cer_s}, ASR {lat_s})")

        if not rows:
            return {}
        cers = [r["cer"] for _, r in rows if r["cer"] is not None]
        lats = [r["asr_ms"] for _, r in rows if r["asr_ms"] is not None]
        return {
            "engine": engine,
            "avg_cer": sum(cers) / len(cers) if cers else None,
            "avg_asr_ms": sum(lats) / len(lats) if lats else None,
            "exact": sum(1 for _, r in rows if r["cer"] == 0),
            "total": len(rows),
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(2)


def main() -> int:
    ap = argparse.ArgumentParser(description="live-path ASR engine comparison")
    ap.add_argument("--engines", nargs="+", default=["sensevoice"])
    ap.add_argument("--voice", default="zh_CN-huayan-medium")
    args = ap.parse_args()

    print("=" * 74)
    print("Live-path ASR comparison (full WebRTC, incl. Opus codec and resampling)")
    print("=" * 74)
    print(f"  sentences: {len(SENTENCES)}  engines: {', '.join(args.engines)}")
    wavs = synthesize(args.voice)
    print(f"  test audio ready: {CACHE}")

    results = []
    for i, engine in enumerate(args.engines):
        r = bench_engine(engine, wavs, 7900 + i)
        if r:
            results.append(r)

    if not results:
        print("\nno engine completed.")
        return 1

    print(f"\n{'=' * 74}")
    print("live-path summary")
    print("=" * 74)
    print(f"{'engine':<16}{'CER':>9}{'exact':>10}{'ASR ms':>11}")
    print("-" * 74)
    for r in sorted(results, key=lambda x: x["avg_cer"] or 9):
        cer_s = "N/A" if r["avg_cer"] is None else f"{r['avg_cer'] * 100:.1f}%"
        lat_s = "N/A" if r["avg_asr_ms"] is None else f"{r['avg_asr_ms']:.0f}ms"
        print(f"{r['engine']:<16}{cer_s:>9}{r['exact']:>7}/{r['total']:<3}{lat_s:>11}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
