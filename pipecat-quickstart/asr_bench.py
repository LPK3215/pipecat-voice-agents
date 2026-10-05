#!/usr/bin/env python3
"""Offline ASR benchmark: quantify Chinese recognition accuracy and latency across configs.

Why a dedicated benchmark:
    The full-path probe (audio_probe.py) takes over two minutes per run and only shows one
    transcript, so it cannot tell you "did a config change actually help". This isolates ASR
    offline: the same sentence set, several configs side by side, and a comparable number --
    **character error rate (CER)**.

The test set is synthesized with Piper:
    The upside is that **the reference answer is known** and no manual transcription is
    needed. The downside is that synthesized speech is "cleaner" than a human, so the CER
    here is an **optimistic lower bound** -- real speech is only worse, never better. For the
    relative comparison "is config A better than config B" the conclusion still holds.

NOTE: SENTENCES and CURRENT_PROMPT below are intentionally Chinese test data / prompts.

Usage:
    cd server && uv run ../asr_bench.py                 # base only (no download)
    cd server && uv run ../asr_bench.py --models small  # also small (downloads ~500MB)
    cd server && uv run ../asr_bench.py --models base small medium
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import wave
from pathlib import Path

BASE = Path(__file__).resolve().parent
CACHE = BASE / ".cache" / "asr_bench"
TARGET_SR = 16000

# Test set: reference answers known. The first sentence matches settings.VERIFY_USER_TEXT so
# it can be compared against the full-path probe.
SENTENCES = [
    "你好，请用一句话介绍一下你自己。",
    "今天北京的天气怎么样？",
    "帮我查一下服务器当前的负载情况。",
    "我想预订明天下午三点的会议室。",
    "请写一段 Python 代码，用来读取文件内容。",
    "这个月的销售额比上个月增长了多少？",
]

# The prompt the current bot.py uses (settings.WHISPER_INITIAL_PROMPT)
CURRENT_PROMPT = "以下是普通话的句子。"


# ---------------------------------------------------------------------------
# Audio preparation
# ---------------------------------------------------------------------------
def synthesize(voice: str) -> list[Path]:
    """Synthesize the test sentences to WAV with Piper (cached)."""
    from piper import PiperVoice
    from piper.download_voices import download_voice

    cache = Path.home() / ".cache" / "pipecat" / "piper"
    onnx = cache / f"{voice}.onnx"
    if not onnx.exists():
        print(f"  downloading Piper voice {voice} ...")
        cache.mkdir(parents=True, exist_ok=True)
        download_voice(voice, cache)

    pv = PiperVoice.load(str(onnx))
    CACHE.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, s in enumerate(SENTENCES):
        p = CACHE / f"s{i}.wav"
        if not p.exists():
            with wave.open(str(p), "wb") as wf:
                pv.synthesize_wav(s, wf)
        paths.append(p)
    return paths


def load_audio(path: Path):
    """Read WAV -> mono 16kHz float32 (same resampling convention as audio_probe.load_pcm)."""
    import numpy as np

    with wave.open(str(path), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != TARGET_SR:
        idx = np.linspace(0, len(a) - 1, int(round(len(a) * TARGET_SR / sr)))
        a = np.interp(idx, np.arange(len(a)), a)
    return a.astype(np.float32)


# ---------------------------------------------------------------------------
# Character error rate
# ---------------------------------------------------------------------------
def normalize(s: str) -> str:
    """Strip punctuation and whitespace, keeping only characters and digits -- so punctuation
    differences do not pollute the CER."""
    return re.sub(r"[^\w]", "", s)


def levenshtein(a: str, b: str) -> int:
    """Minimum edit distance between two strings (character level)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    """Character error rate = edit distance / reference length. 0 means perfect."""
    r, h = normalize(ref), normalize(hyp)
    if not r:
        return 0.0
    return levenshtein(r, h) / len(r)


# ---------------------------------------------------------------------------
# Run the benchmark
# ---------------------------------------------------------------------------
def run_config(model: str, language: str | None, prompt: str | None, audios) -> dict:
    from faster_whisper import WhisperModel

    label = model + ("+zh" if language else "+auto")
    label += "+prompt" if prompt else ""

    t0 = time.perf_counter()
    m = WhisperModel(model, device="cpu", compute_type="int8")
    load_s = time.perf_counter() - t0

    results, total_s = [], 0.0
    for ref, audio in zip(SENTENCES, audios):
        t1 = time.perf_counter()
        segs, _ = m.transcribe(
            audio, language=language, initial_prompt=prompt, beam_size=5
        )
        text = "".join(s.text for s in segs)
        total_s += time.perf_counter() - t1
        results.append((ref, text, cer(ref, text)))

    avg_cer = sum(c for _, _, c in results) / len(results)
    exact = sum(1 for _, _, c in results if c == 0)
    return {
        "label": label,
        "model": model,
        "language": language,
        "avg_cer": avg_cer,
        "exact": exact,
        "total": len(results),
        "load_s": load_s,
        "per_audio_s": total_s / len(results),
        "results": results,
    }


def run_sensevoice(audios) -> dict:
    """Alibaba SenseVoice (non-autoregressive, strong on Chinese), via FunASR."""
    from funasr import AutoModel
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    t0 = time.perf_counter()
    m = AutoModel(model="iic/SenseVoiceSmall", trust_remote_code=True, device="cpu")
    load_s = time.perf_counter() - t0

    results, total_s = [], 0.0
    for ref, audio in zip(SENTENCES, audios):
        t1 = time.perf_counter()
        res = m.generate(input=audio, language="zh", use_itn=True)
        total_s += time.perf_counter() - t1
        text = rich_transcription_postprocess(res[0]["text"]) if res else ""
        results.append((ref, text, cer(ref, text)))

    avg_cer = sum(c for _, _, c in results) / len(results)
    return {
        "label": "sensevoice",
        "model": "sensevoice",
        "language": "zh",
        "avg_cer": avg_cer,
        "exact": sum(1 for _, _, c in results if c == 0),
        "total": len(results),
        "load_s": load_s,
        "per_audio_s": total_s / len(results),
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="ASR Chinese recognition benchmark")
    ap.add_argument(
        "--models",
        nargs="+",
        default=["base"],
        help="Whisper models to compare (default base; small/medium require downloads)",
    )
    ap.add_argument("--voice", default="zh_CN-huayan-medium", help="Piper voice for the test audio")
    args = ap.parse_args()

    print("=" * 74)
    print("ASR benchmark (test set = Piper-synthesized speech, references known)")
    print("=" * 74)
    print(f"  sentences: {len(SENTENCES)}  models: {', '.join(args.models)}")
    print("  preparing audio ...", flush=True)
    audios = [load_audio(p) for p in synthesize(args.voice)]
    print("  starting comparison\n")

    runs = []
    for model in args.models:
        if model == "sensevoice":
            try:
                runs.append(run_sensevoice(audios))
            except Exception as exc:  # noqa: BLE001
                print(f"  [skip] sensevoice: {exc}")
            continue
        # Two key variables: whether Chinese is specified explicitly, and whether a prompt is given.
        for lang, prompt in ((None, CURRENT_PROMPT), ("zh", CURRENT_PROMPT)):
            try:
                runs.append(run_config(model, lang, prompt, audios))
            except Exception as exc:  # noqa: BLE001
                print(f"  [skip] {model} lang={lang}: {exc}")

    if not runs:
        print("no configuration completed.")
        return 1

    # ---- summary ----
    print("=" * 74)
    print(f"{'config':<22}{'CER':>9}{'exact':>10}{'per-sentence':>14}{'load':>9}")
    print("-" * 74)
    for r in sorted(runs, key=lambda x: x["avg_cer"]):
        print(
            f"{r['label']:<22}{r['avg_cer'] * 100:>8.1f}%"
            f"{r['exact']:>7}/{r['total']:<3}"
            f"{r['per_audio_s'] * 1000:>11.0f}ms"
            f"{r['load_s']:>8.1f}s"
        )
    print("=" * 74)

    best = min(runs, key=lambda x: x["avg_cer"])
    cur = next((r for r in runs if r["language"] is None and r["model"] == "base"), None)
    if cur and best["label"] != cur["label"]:
        drop = cur["avg_cer"] - best["avg_cer"]
        print(f"\nbest: {best['label']} (CER {best['avg_cer'] * 100:.1f}%)")
        print(
            f"current: {cur['label']} (CER {cur['avg_cer'] * 100:.1f}%)"
            f" -> switching to the best config lowers it by {drop * 100:.1f} points"
        )

    # ---- per-sentence detail (best config) ----
    print(f"\nper-sentence detail ({best['label']}):")
    for ref, hyp, c in best["results"]:
        flag = "OK " if c == 0 else "ERR"
        print(f"  [{flag}] reference: {ref}")
        if c:
            print(f"        transcript: {hyp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
