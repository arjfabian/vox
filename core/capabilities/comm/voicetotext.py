"""
core/capabilities/comm/voicetotext.py

Transcribes audio bytes to text using faster-whisper (local, offline).
Model is loaded once at boot and kept resident — no per-call loading cost.

Optimized for short voice commands in ES/EN/PT on CPU-only VPS.
Default model: tiny (150MB disk, ~200MB RAM, ~1-2s per utterance).
"""

from core.capability import VOXCapability
from core.logger import log_info, log_warn, log_fail


class Capability(VOXCapability):

    PARAMS = {
        "WHISPER_MODEL":   ["faster-whisper model size (tiny/base/small)", "tiny"],
        "WHISPER_DEVICE":  ["Compute device: cpu or cuda",                 "cpu"],
        "WHISPER_COMPUTE": ["Compute type: int8, float16, float32",        "int8"],
    }

    async def health_check(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            log_fail(
                "faster-whisper not installed. Run: pip install faster-whisper",
                "comm.voicetotext"
            )
            return False

    async def boot(self):
        """
        Loads the Whisper model once and keeps it resident.
        Avoids the cost of loading ~150MB on every transcription call.
        """
        try:
            from faster_whisper import WhisperModel

            model_size   = self.WHISPER_MODEL
            device       = self.WHISPER_DEVICE
            compute_type = self.WHISPER_COMPUTE

            log_info(
                f"Loading Whisper [{model_size}] on {device}/{compute_type}...",
                "comm.voicetotext"
            )
            self._model = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type
            )
            log_info("Whisper model ready.", "comm.voicetotext")

        except Exception as e:
            log_fail(f"Failed to load Whisper model: {e}", "comm.voicetotext")
            self._model = None

    async def transcribe(self, audio_bytes: bytes) -> str:
        """
        Transcribes audio_bytes to text using the resident model.
        Language is auto-detected per utterance — no configuration needed.
        Returns empty string on failure or if model is not loaded.
        """
        if not audio_bytes:
            return ""

        if not hasattr(self, "_model") or self._model is None:
            log_warn("Whisper model not loaded. Transcription skipped.", "comm.voicetotext")
            return ""

        try:
            import io
            import soundfile as sf

            audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes))

            segments, info = self._model.transcribe(
                audio_data,
                beam_size=5,
                language=None,   # auto-detect per utterance
                vad_filter=True, # skip silence
            )

            text = " ".join(seg.text.strip() for seg in segments).strip()

            log_info(
                f"Transcribed [{info.language} {info.language_probability:.0%}]: "
                f"'{text[:60]}{'...' if len(text) > 60 else ''}'",
                "comm.voicetotext"
            )

            return text

        except Exception as e:
            log_fail(f"Transcription error: {e}", "comm.voicetotext")
            return ""