#!/usr/bin/env python3
"""真实链路 ASR 对比：同一批句子，分别用不同 STT 引擎跑完整的 WebRTC 链路。

为什么需要它（离线基准 asr_bench.py 不够）：
    离线基准把 WAV **直接喂给模型**，跳过了真实链路里的 Opus 编解码、
    WebRTC 抖动缓冲和多次重采样。实测发现离线满分的配置，在真实链路里
    照样认错 —— 所以只有走完整链路测出来的字错率才是可信的。

做法：
    拉起一个 bot.py（用 STT_ENGINE 环境变量指定引擎），然后对每一句测试音频
    跑一次 audio_probe.py（--no-spawn 复用同一个服务），收集识别文本与延迟。

用法:
    cd server && uv run ../live_asr_bench.py                      # 只测默认引擎
    cd server && uv run ../live_asr_bench.py --engines sensevoice whisper
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
SERVER = BASE / "server"
PY = SERVER / ".venv" / "bin" / "python"

sys.path.insert(0, str(BASE))
from asr_bench import CACHE, SENTENCES, cer, synthesize  # noqa: E402


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
    """跑一句，从探针输出里解析出识别文本、字错率、ASR 延迟。

    audio_probe 的服务地址取自 ``PROBE_PORT``，必须和 bot 实际监听的端口一致，
    否则它会连到默认端口（那里没有服务），静默拿不到任何结果。
    """
    proc = subprocess.run(
        [
            str(PY),
            str(BASE / "audio_probe.py"),
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
    if "音频未发送完成" in out or proc.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-6:])
        print(f"    [探针失败 rc={proc.returncode}] {tail}")
        if proc.stderr.strip():
            print(f"    [stderr] {proc.stderr.strip().splitlines()[-1][:200]}")
    m_cer = re.search(r"字错率\s*:\s*([\d.]+)%", out)
    m_lat = re.search(r"收到识别文本\s+(\d+)\s*ms", out)
    m_txt = re.search(r"识别文本\s*:\s*(.*)", out)
    return {
        "text": m_txt.group(1).strip() if m_txt else "",
        "cer": float(m_cer.group(1)) / 100 if m_cer else None,
        "asr_ms": int(m_lat.group(1)) if m_lat else None,
    }


def bench_engine(engine: str, wavs: list[Path], port: int) -> dict:
    print(f"\n{'=' * 74}")
    print(f"引擎: {engine}")
    print("=" * 74)
    proc = spawn_bot(engine, port)
    try:
        if not wait_up(f"http://127.0.0.1:{port}/"):
            print("  bot.py 未就绪，跳过该引擎")
            return {}
        print("  服务已就绪，开始逐句测试…")

        rows = []
        for ref, wav in zip(SENTENCES, wavs):
            try:
                r = run_one(wav, ref, port)
            except subprocess.TimeoutExpired:
                print(f"  超时: {ref}")
                continue
            rows.append((ref, r))
            cer_s = "N/A" if r["cer"] is None else f"{r['cer'] * 100:.1f}%"
            lat_s = "N/A" if r["asr_ms"] is None else f"{r['asr_ms']}ms"
            flag = "OK " if r["cer"] == 0 else "ERR"
            print(f"  [{flag}] {ref}")
            print(f"        识别={r['text']}  (CER {cer_s}, ASR {lat_s})")

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
    ap = argparse.ArgumentParser(description="真实链路 ASR 引擎对比")
    ap.add_argument("--engines", nargs="+", default=["sensevoice"])
    ap.add_argument("--voice", default="zh_CN-huayan-medium")
    args = ap.parse_args()

    print("=" * 74)
    print("真实链路 ASR 对比（走完整 WebRTC，含 Opus 编解码与重采样）")
    print("=" * 74)
    print(f"  测试句数: {len(SENTENCES)}  引擎: {', '.join(args.engines)}")
    wavs = synthesize(args.voice)
    print(f"  测试音频已就绪: {CACHE}")

    results = []
    for i, engine in enumerate(args.engines):
        r = bench_engine(engine, wavs, 7900 + i)
        if r:
            results.append(r)

    if not results:
        print("\n没有引擎跑通。")
        return 1

    print(f"\n{'=' * 74}")
    print("真实链路汇总")
    print("=" * 74)
    print(f"{'引擎':<16}{'字错率':>9}{'完全正确':>10}{'ASR 耗时':>11}")
    print("-" * 74)
    for r in sorted(results, key=lambda x: x["avg_cer"] or 9):
        cer_s = "N/A" if r["avg_cer"] is None else f"{r['avg_cer'] * 100:.1f}%"
        lat_s = "N/A" if r["avg_asr_ms"] is None else f"{r['avg_asr_ms']:.0f}ms"
        print(f"{r['engine']:<16}{cer_s:>9}{r['exact']:>7}/{r['total']:<3}{lat_s:>11}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
