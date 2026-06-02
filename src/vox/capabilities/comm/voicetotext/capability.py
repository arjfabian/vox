"""Voice-to-text capability runner."""

import logging

from vox.capabilities.base import VOXCapability

from .client import WhisperClient
from .models import WhisperConfig


LOG_PREVIEW_LENGTH = 60


class VoiceToTextCapability(VOXCapability):

    CAPABILITY_NAME = "comm.voicetotext"

    PARAMS = {
        "WHISPER_MODEL": ["faster-whisper model size", "base"],
        "WHISPER_DEVICE": ["Compute device", "cpu"],
        "WHISPER_COMPUTE": ["Compute precision", "int8"],
    }

    @classmethod
    async def health_check(cls) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            logger = logging.getLogger(__name__)
            logger.error("faster-whisper not installed")
            return False

    def _build_client(self) -> WhisperClient:
        config = WhisperConfig(
            model=self.PARAMS["WHISPER_MODEL"][1],
            device=self.PARAMS["WHISPER_DEVICE"][1],
            compute_type=self.PARAMS["WHISPER_COMPUTE"][1],
        )
        return WhisperClient(config)

    async def transcribe(self, audio_bytes: bytes) -> str:
        if not audio_bytes:
            return ""
        self.logger.info("Transcribing audio...")
        client = self._build_client()
        result = await client.transcribe(audio_bytes)
        preview = result.text[:LOG_PREVIEW_LENGTH]
        suffix = "..." if len(result.text) > LOG_PREVIEW_LENGTH else ""
        self.logger.info(
            f"Transcribed [{result.language} {result.confidence:.0%}]: "
            f"'{preview}{suffix}'"
        )
        return result.text

    async def run(self, audio_bytes: bytes) -> str:
        return await self.transcribe(audio_bytes)
