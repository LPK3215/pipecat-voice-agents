"""Raw 16-bit mono PCM over a websocket -- the simplest possible audio contract.

The framework ships serializers for telephony vendors (Twilio, Plivo, ...) but none for
"just PCM", which is what a browser page or another agent can produce with no dependencies.

Why this exists at all: WebRTC media needs UDP, and UDP does not survive ordinary HTTP port
forwarding (cloud IDEs, reverse proxies, phone systems). WebSocket runs on TCP, so it works
everywhere the page itself works -- and it is the shape other systems expect when they embed
the module.
"""

from __future__ import annotations

import json

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer


class RawPCMFrameSerializer(FrameSerializer):
    """16-bit signed PCM, mono, at a fixed sample rate, no framing beyond the websocket.

    Two kinds of frame share the socket, separated by **type**, not by a header:

        binary frame = PCM audio          (the contract below, unchanged)
        text frame   = one JSON event     (what the platform is doing, for the page to show)

    Text frames are additive: a client that only reads binary still works exactly as before.

    **Filter by a key you know.** The pipeline also emits its own client-protocol messages on
    this socket (`{"label": "rtvi-ai", "type": ...}` -- metrics, transcription, speaking state).
    They are not part of this contract and differ per transport, so a client should ignore any
    text frame that does not carry the field it is looking for (`kind`, in the page's case).
    """

    def __init__(self, sample_rate: int = 16000):
        super().__init__()
        self._sample_rate = sample_rate

    async def serialize(self, frame: Frame) -> str | bytes | None:
        """Frames going out to the client: audio as raw bytes, process events as JSON text."""
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            return json.dumps(frame.message, ensure_ascii=False)
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        """Frames coming in from the client: raw bytes are the user's microphone.

        Text frames from the client are ignored on purpose -- the module takes audio in and
        nothing else; there is no second control channel to get out of sync.
        """
        if isinstance(data, (bytes, bytearray)) and len(data) > 0:
            return InputAudioRawFrame(
                audio=bytes(data),
                sample_rate=self._sample_rate,
                num_channels=1,
            )
        return None

    async def setup(self, frame: StartFrame) -> None:
        """Take the sample rate from the pipeline rather than assuming it."""
        await super().setup(frame)
        if getattr(frame, "audio_in_sample_rate", None):
            self._sample_rate = frame.audio_in_sample_rate
