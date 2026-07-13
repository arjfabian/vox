"""faster-whisper runtime client.

Isolates the synchronous faster-whisper inference pipeline behind
an async facade using asyncio.to_thread for CPU-bound operations.
No top-level synchronization locks.
"""

import asyncio
import io

import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel

from .models import WhisperConfig, TranscriptionResult


WHISPER_BEAM_SIZE = 5


class WhisperClient:

    def __init__(self, config: WhisperConfig) -> None:
        self.config = config
        self._model: WhisperModel | None = None

    def _load_model(self) -> WhisperModel:
        if self._model is None:
            self._model = WhisperModel(
                self.config.model,
                device=self.config.device,
                compute_type=self.config.compute_type,
            )
        return self._model

    def _preprocess(self, audio_data: np.ndarray, sample_rate: int) -> np.ndarray:
        import librosa
        if audio_data.ndim > 1:
            audio_data = np.mean(audio_data, axis=1)
        if sample_rate != 16000:
            audio_data = librosa.resample(
                audio_data, orig_sr=sample_rate, target_sr=16000,
            )
        audio_data = audio_data - np.mean(audio_data)
        rms = np.sqrt(np.mean(audio_data ** 2))
        if rms > 0:
            audio_data = audio_data * (0.1 / rms)
        return np.clip(audio_data, -1.0, 1.0).astype(np.float32)

    def _transcribe_sync(self, audio_bytes: bytes) -> TranscriptionResult:
        audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
        audio_data = self._preprocess(audio_data, sample_rate)

        model = self._load_model()
        segments, info = model.transcribe(
            audio_data,
            beam_size=WHISPER_BEAM_SIZE,
            language=None,
            vad_filter=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return TranscriptionResult(
            text=text,
            language=info.language,
            confidence=info.language_probability,
        )

    async def transcribe(self, audio_bytes: bytes) -> TranscriptionResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._transcribe_sync, audio_bytes,
        )
