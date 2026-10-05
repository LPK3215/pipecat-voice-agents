"""预热：提前把本地模型下载/加载一次，让之后启动的会话不再卡在下载上。

为什么需要：
    STT（Whisper / SenseVoice）与 TTS（Piper 音色）都是本地模型，首次使用要联网
    下载（Whisper base 约 150MB、SenseVoice 约 940MB、Piper 音色数十 MB）。
    下载发生在**会话建立时**，用户的第一句话会因此长时间没有响应 —— 现象是
    「连上了、说话了，但机器人一直不理」。

    先跑一次本脚本把模型拉进本地缓存，之后启动即快。

用法：
    cd server && uv run ../prewarm.py
"""

import os
import sys
import wave
from pathlib import Path

BASE = Path(__file__).resolve().parent
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
    """触发 Piper 音色下载并合成一句，确保 TTS 首次调用不再下载。"""
    from piper import PiperVoice
    from piper.download_voices import download_voice

    voice = os.getenv("PIPER_VOICE_ID", DEFAULT_PIPER_VOICE)
    cache = Path.home() / ".cache" / "pipecat" / "piper"
    onnx = cache / f"{voice}.onnx"
    if not onnx.exists():
        logger.info(f"[PREWARM] 下载 Piper 音色 {voice} …")
        cache.mkdir(parents=True, exist_ok=True)
        download_voice(voice, cache)
    logger.info(f"[PREWARM] 合成一句测试音（{voice}）…")
    pv = PiperVoice.load(str(onnx))
    out = cache / "_prewarm.wav"
    with wave.open(str(out), "wb") as wf:
        pv.synthesize_wav(WARM_TEXT, wf)
    logger.info(f"[PREWARM] TTS 就绪（{out.name}，{out.stat().st_size} 字节）")


def main() -> int:
    print("=" * 70)
    print("本地模型预热（下载/加载一次，之后启动即快）")
    print("=" * 70)

    ok = True

    try:
        logger.info("[PREWARM] 构造 STT（首次会下载模型，可能较久）…")
        _, sdesc = build_stt()
        print(f"  ✅ STT: {sdesc}")
    except Exception as exc:  # noqa: BLE001 - 预热失败要给出可读原因
        ok = False
        print(f"  ❌ STT 预热失败: {type(exc).__name__}: {exc}")

    try:
        _, tdesc = build_tts()
        print(f"  ✅ TTS: {tdesc}")
        if "Piper" in tdesc:
            warm_tts_voice()
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  ❌ TTS 预热失败: {type(exc).__name__}: {exc}")

    print("=" * 70)
    print("预热完成 ✅" if ok else "预热存在失败项 ❌（见上）")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
