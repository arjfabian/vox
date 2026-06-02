"""faster-whisper runtime client."""

import io
import numpy as np
import soundfile as sf

from faster_whisper import WhisperModel

from .models import WhisperConfig, TranscriptionResult


WHISPER_BEAM_SIZE = 5


class WhisperClient:

    def __init__(self, config: WhisperConfig) -> None:
        self.config = config
        self.model = WhisperModel(
            config.model,
            device=config.device,
            compute_type=config.compute_type,
        )

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

    async def transcribe(self, audio_bytes: bytes) -> TranscriptionResult:
        audio_data, sample_rate = sf.read(
            io.BytesIO(audio_bytes), dtype="float32",
        )
        audio_data = self._preprocess(audio_data, sample_rate)
        segments, info = self.model.transcribe(
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
