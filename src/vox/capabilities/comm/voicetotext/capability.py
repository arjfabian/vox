"""comm.voicetotext — Local offline speech-to-text capability.

Orchestration layer for faster-whisper transcription.
WhisperClient is allocated once during boot() and reused across
all transcribe() calls, with CPU-bound inference offloaded
to a thread pool executor.
"""

from vox.capabilities.base import VOXCapability

from .client import WhisperClient
from .models import TranscriptionResult, WhisperConfig

LOG_PREVIEW_LENGTH = 60


class VoiceToTextCapability(VOXCapability):
    CAPABILITY_NAME = "comm.voicetotext"

    _client: WhisperClient

    @classmethod
    async def health_check(cls) -> bool:
        try:
            import faster_whisper  # noqa: F401

            return True
        except ImportError:
            return False

    def _build_client(self) -> WhisperClient:
        config = WhisperConfig(
            model=self.WHISPER_MODEL_SIZE,
            device=self.WHISPER_DEVICE,
            compute_type=self.WHISPER_COMPUTE_TYPE,
        )
        return WhisperClient(config)

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    async def transcribe(self, file_path: str) -> TranscriptionResult:
        self.log("Transcribing audio...")
        with open(file_path, "rb") as f:  # noqa: ASYNC230 — small file read, blocking negligible
            audio_bytes = f.read()
        result = await self._client.transcribe(audio_bytes)
        preview = result.text[:LOG_PREVIEW_LENGTH]
        suffix = "..." if len(result.text) > LOG_PREVIEW_LENGTH else ""
        self.log(
            f"Transcribed [{result.language} {result.confidence:.0%}]: "
            f"'{preview}{suffix}'"
        )
        return result

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def boot(self) -> None:
        self._client = self._build_client()

    async def shutdown(self) -> None:
        pass
