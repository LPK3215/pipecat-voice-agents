#!/usr/bin/env python3
"""ASR（语音识别）离线基准：量化不同配置下的中文识别准确率与耗时。

为什么单独做这个：
    全链路探针（audio_probe.py）一次要两分多钟，而且只能看到一条识别结果，
    无法判断「换配置到底有没有变好」。这里把 ASR 单独拎出来离线测：
    同一批句子、多种配置横向对比，用**字错率（CER）**给一个可比较的数字。

测试集用 Piper 合成：
    好处是**标准答案确定已知**，不用人工听写。代价是合成语音比真人更「干净」，
    所以这里的 CER 是**乐观下界** —— 真人语音只会更差，不会更好。
    但对「A 配置是否优于 B 配置」这个相对比较，结论依然成立。

用法:
    cd server && uv run ../asr_bench.py                 # 只测 base（无下载）
    cd server && uv run ../asr_bench.py --models small  # 额外测 small（需下载约 500MB）
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

# 测试集：标准答案已知。第一句与 settings.VERIFY_USER_TEXT 一致，便于和全链路对照。
SENTENCES = [
    "你好，请用一句话介绍一下你自己。",
    "今天北京的天气怎么样？",
    "帮我查一下服务器当前的负载情况。",
    "我想预订明天下午三点的会议室。",
    "请写一段 Python 代码，用来读取文件内容。",
    "这个月的销售额比上个月增长了多少？",
]

# 当前 bot.py 用的提示词（settings.WHISPER_INITIAL_PROMPT）
CURRENT_PROMPT = "以下是普通话的句子。"


# ---------------------------------------------------------------------------
# 音频准备
# ---------------------------------------------------------------------------
def synthesize(voice: str) -> list[Path]:
    """用 Piper 把测试句合成为 WAV（带缓存）。"""
    from piper import PiperVoice
    from piper.download_voices import download_voice

    cache = Path.home() / ".cache" / "pipecat" / "piper"
    onnx = cache / f"{voice}.onnx"
    if not onnx.exists():
        print(f"  下载 Piper 音色 {voice} …")
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
    """读 WAV → 单声道 16kHz float32（与 audio_probe.load_pcm 同一套重采样口径）。"""
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
# 字错率
# ---------------------------------------------------------------------------
def normalize(s: str) -> str:
    """去掉标点与空白，只保留文字与数字 —— 避免标点差异污染 CER。"""
    return re.sub(r"[^\w]", "", s)


def levenshtein(a: str, b: str) -> int:
    """两字符串的最小编辑距离（字符级）。"""
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
    """字错率 = 编辑距离 / 标准答案字数。0 表示完全正确。"""
    r, h = normalize(ref), normalize(hyp)
    if not r:
        return 0.0
    return levenshtein(r, h) / len(r)


# ---------------------------------------------------------------------------
# 跑基准
# ---------------------------------------------------------------------------
def run_config(model: str, language: str | None, prompt: str | None, audios) -> dict:
    from faster_whisper import WhisperModel

    label = model + ("+zh" if language else "+自动")
    label += "+提示词" if prompt else ""

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
    """阿里 SenseVoice（非自回归，中文强项）。经 pipecat 的 FunASRSTTService 调用。"""
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
    ap = argparse.ArgumentParser(description="ASR 中文识别基准")
    ap.add_argument(
        "--models",
        nargs="+",
        default=["base"],
        help="要对比的 Whisper 模型（默认 base；small/medium 需下载）",
    )
    ap.add_argument("--voice", default="zh_CN-huayan-medium", help="Piper 测试音色")
    args = ap.parse_args()

    print("=" * 74)
    print("ASR 基准（测试集 = Piper 合成语音，标准答案已知）")
    print("=" * 74)
    print(f"  测试句数: {len(SENTENCES)}  模型: {', '.join(args.models)}")
    print("  准备音频…", flush=True)
    audios = [load_audio(p) for p in synthesize(args.voice)]
    print("  开始对比\n")

    runs = []
    for model in args.models:
        if model == "sensevoice":
            try:
                runs.append(run_sensevoice(audios))
            except Exception as exc:  # noqa: BLE001
                print(f"  [跳过] sensevoice: {exc}")
            continue
        # 两种关键变量：是否显式指定中文、是否给提示词
        for lang, prompt in ((None, CURRENT_PROMPT), ("zh", CURRENT_PROMPT)):
            try:
                runs.append(run_config(model, lang, prompt, audios))
            except Exception as exc:  # noqa: BLE001
                print(f"  [跳过] {model} lang={lang}: {exc}")

    if not runs:
        print("没有任何配置跑通。")
        return 1

    # ---- 汇总 ----
    print("=" * 74)
    print(f"{'配置':<22}{'字错率':>9}{'完全正确':>10}{'单句耗时':>11}{'加载':>9}")
    print("-" * 74)
    for r in sorted(runs, key=lambda x: x["avg_cer"]):
        print(
            f"{r['label']:<22}{r['avg_cer'] * 100:>8.1f}%"
            f"{r['exact']:>7}/{r['total']:<3}"
            f"{r['per_audio_s'] * 1000:>9.0f}ms"
            f"{r['load_s']:>8.1f}s"
        )
    print("=" * 74)

    best = min(runs, key=lambda x: x["avg_cer"])
    cur = next((r for r in runs if r["language"] is None and r["model"] == "base"), None)
    if cur and best["label"] != cur["label"]:
        drop = cur["avg_cer"] - best["avg_cer"]
        print(f"\n最优: {best['label']}（字错率 {best['avg_cer'] * 100:.1f}%）")
        print(
            f"当前: {cur['label']}（字错率 {cur['avg_cer'] * 100:.1f}%）"
            f" → 换用最优配置可降 {drop * 100:.1f} 个百分点"
        )

    # ---- 逐句明细（最优配置）----
    print(f"\n逐句明细（{best['label']}）:")
    for ref, hyp, c in best["results"]:
        flag = "OK " if c == 0 else "ERR"
        print(f"  [{flag}] 标准: {ref}")
        if c:
            print(f"        识别: {hyp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
