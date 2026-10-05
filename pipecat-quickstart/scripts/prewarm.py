"""Prewarm: download/load the local models once so later sessions do not stall on downloads.

Why it is needed:
    STT (Whisper / SenseVoice) and TTS (Piper voices) are local models; the first use
    downloads them (Whisper base ~150MB, SenseVoice ~940MB, Piper voices tens of MB). The
    download happens **when the session starts**, so the user's first sentence gets no
    response for a long time -- the symptom is "it connected, I spoke, but the bot never
    answers".

    Run this script once to pull the models into the local cache; later starts are fast.

Usage:
    cd server && uv run ../scripts/prewarm.py
"""

import os
import sys
import wave
from pathlib import Path

# Scripts live in scripts/; the project root (server/, sample-data/, docs/) is one level up.
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "server"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE / "server" / ".env", override=True)

from loguru import logger  # noqa: E402

from settings import (  # noqa: E402
    DEFAULT_PIPER_VOICE,
    build_stt,
    build_tts,
)

WARM_TEXT = "预热。"


def warm_tts_voice() -> None:
    """Trigger the Piper voice download and synthesize one sentence, so TTS no longer downloads."""
    from piper import PiperVoice
    from piper.download_voices import download_voice

    voice = os.getenv("PIPER_VOICE_ID", DEFAULT_PIPER_VOICE)
    cache = Path.home() / ".cache" / "pipecat" / "piper"
    onnx = cache / f"{voice}.onnx"
    if not onnx.exists():
        logger.info(f"[PREWARM] downloading Piper voice {voice} ...")
        cache.mkdir(parents=True, exist_ok=True)
        download_voice(voice, cache)
    logger.info(f"[PREWARM] synthesizing a test sentence ({voice}) ...")
    pv = PiperVoice.load(str(onnx))
    out = cache / "_prewarm.wav"
    with wave.open(str(out), "wb") as wf:
        pv.synthesize_wav(WARM_TEXT, wf)
    logger.info(f"[PREWARM] TTS ready ({out.name}, {out.stat().st_size} bytes)")


def main() -> int:
    print("=" * 70)
    print("Local model prewarm (download/load once; later starts are fast)")
    print("=" * 70)

    ok = True

    try:
        logger.info("[PREWARM] building STT (first run downloads the model, may take a while) ...")
        _, sdesc = build_stt()
        print(f"  [OK] STT: {sdesc}")
    except Exception as exc:  # noqa: BLE001 - prewarm failures must give a readable reason
        ok = False
        print(f"  [FAIL] STT prewarm: {type(exc).__name__}: {exc}")

    try:
        _, tdesc = build_tts()
        print(f"  [OK] TTS: {tdesc}")
        if "Piper" in tdesc:
            warm_tts_voice()
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  [FAIL] TTS prewarm: {type(exc).__name__}: {exc}")

    print("=" * 70)
    print("prewarm complete [OK]" if ok else "prewarm had failures [FAIL] (see above)")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
