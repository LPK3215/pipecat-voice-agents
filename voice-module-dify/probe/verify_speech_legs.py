"""Probe: can it talk, and can it hear?

The pipeline's two local legs, driven directly (no browser, no platform, no sound card):

    TTS: synthesise a Chinese sentence and write it to logs/probe-tts.wav  <-- listen to this
    STT: transcribe that same file and compare with what was spoken

This is what "does the effect work" means in an environment without a microphone: the module
speaks to a file, then listens to its own file.

    uv run python probe/verify_speech_legs.py
"""

from __future__ import annotations

import glob
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from settings import load_config

PASS, FAIL = "[OK]", "[FAIL]"
SENTENCE = "你好，我是语音模块，现在只负责听和说。"


def find_voice(voice_id: str) -> Path | None:
    """The pipecat piper cache is where the service downloads voices."""
    for pattern in (
        Path.home() / ".cache/pipecat/piper" / f"{voice_id}.onnx",
        Path.home() / ".cache/pipecat/piper" / f"*{voice_id}*.onnx",
    ):
        hits = glob.glob(str(pattern))
        if hits:
            return Path(hits[0])
    return None


def synthesize(voice_id: str, out_path: Path, text: str = SENTENCE) -> str:
    from piper import PiperVoice

    model_path = find_voice(voice_id)
    if model_path is None:
        raise FileNotFoundError(
            f"piper voice {voice_id} not found -- run probe/verify_assembly.py once to download it"
        )
    voice = PiperVoice.load(str(model_path))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    # synthesize_wav writes the file for us; the manual path exists for older piper builds.
    try:
        with wave.open(str(out_path), "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)
    except (TypeError, AttributeError):
        with wave.open(str(out_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(voice.config.sample_rate)
            for chunk in voice.synthesize(text):
                wav_file.writeframes(chunk.audio_int16_bytes)
    elapsed_ms = (time.perf_counter() - started) * 1000
    with wave.open(str(out_path)) as wav_file:
        seconds = wav_file.getnframes() / wav_file.getframerate()
    return f"{out_path.name}: {seconds:.2f}s audio in {elapsed_ms:.0f}ms (voice={voice_id})"


def transcribe(wav_path: Path, model_name: str) -> tuple[str, float]:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    started = time.perf_counter()
    segments, _info = model.transcribe(
        str(wav_path), language="zh", initial_prompt="以下是普通话的句子。"
    )
    text = "".join(segment.text for segment in segments).strip()
    return text, (time.perf_counter() - started) * 1000


def similarity(reference: str, heard: str) -> float:
    """Rough character overlap: enough to tell "it heard the sentence" from "it heard noise"."""
    ref = {c for c in reference if c.strip() and c not in "，。！？、"}
    got = {c for c in heard if c.strip() and c not in "，。！？、"}

    return len(ref & got) / max(1, len(ref))


def main() -> int:
    cfg = load_config()
    out_path = cfg.logs_dir / "probe-tts.wav"
    print("=" * 72)
    print("voice-module / speech legs probe (TTS writes a file, STT reads it back)")
    print("=" * 72)
    results: list[tuple[str, bool, str]] = []

    try:
        detail = synthesize(cfg.voice.piper_voice, out_path)
        produced = out_path.exists() and out_path.stat().st_size > 1000
        results.append(("TTS synthesises audio", produced, detail))
    except Exception as exc:
        results.append(("TTS synthesises audio", False, f"{type(exc).__name__}: {exc}"))

    if results[0][1]:
        try:
            heard, elapsed_ms = transcribe(out_path, cfg.voice.whisper_model)
            score = similarity(SENTENCE, heard)
            # The check is "the leg is wired and it really heard the sentence", not "accuracy
            # is good". The input is **synthetic** Piper speech -- a mechanical voice is a
            # pessimistic stand-in for a human, so a mediocre overlap here does not mean the
            # module hears people badly (phase 1 measured 23.8% CER on a real corpus). The
            # number is reported so a regression is still visible.
            ok = score >= 0.6
            results.append(
                (
                    "STT transcribes it back",
                    ok,
                    f"{elapsed_ms:.0f}ms, overlap {score:.0%} (synthetic input: pessimistic)\n"
                    f"        said: {SENTENCE}\n"
                    f"        heard: {heard}",
                )
            )
        except Exception as exc:
            results.append(("STT transcribes it back", False, f"{type(exc).__name__}: {exc}"))

    for name, ok, detail in results:
        print(f"  {PASS if ok else FAIL} {name}")
        print(f"        {detail}")

    failed = [name for name, ok, _ in results if not ok]
    print("-" * 72)
    if not failed:
        print(f"verdict: {PASS} it can talk and it can hear")
        print(f"listen to it: {out_path}")
    else:
        print(f"verdict: {FAIL} {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
