"""Operator voice identity — loads a pre-enrolled voice embedding and
verifies incoming audio against it using cosine similarity.

System-level infrastructure, not an agent capability.
VOXOrchestrator owns one instance and exposes it to capabilities that
need speaker verification (e.g. comm.gateway).
"""

import io
import numpy as np
from typing import Optional
from vox.observability import VOXForensicLogger

_SIMILARITY_THRESHOLD = 0.7


class VOXSpeakerProfile:
    """Wraps the operator's voice embedding.
    Read access after load() is stateless and safe for concurrent use.
    """

    def __init__(self, identity_dir: str, logger: VOXForensicLogger) -> None:
        self._embedding: Optional[np.ndarray] = None
        self._identity_dir = identity_dir
        self._logger = logger

    @property
    def is_enrolled(self) -> bool:
        return self._embedding is not None

    def load(self) -> bool:
        path = self._identity_dir / "master_voice.npy"
        if not path.exists():
            self._logger.warning(
                f"No speaker profile found at '{path}'. "
                "Voice commands disabled. "
                "Run: python tools/enroll_speaker.py"
            )
            return False
        try:
            self._embedding = np.load(path)
            self._logger.ok(f"Speaker profile loaded from '{path}'.")
            return True
        except Exception as e:
            self._logger.error(f"Failed to load speaker profile: {e}")
            return False

    def verify(self, audio_bytes: bytes) -> bool:
        if not self.is_enrolled or not audio_bytes:
            return False
        try:
            from resemblyzer import VoiceEncoder, preprocess_wav
            import soundfile as sf

            audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes))
            wav = preprocess_wav(audio_data, source_sr=sample_rate)
            encoder = VoiceEncoder()
            embedding = encoder.embed_utterance(wav)
            similarity = self._cosine_similarity(self._embedding, embedding)
            self._logger.info(f"Speaker similarity score: {similarity:.3f}")
            return similarity >= _SIMILARITY_THRESHOLD
        except Exception as e:
            self._logger.error(f"Speaker verification error: {e}")
            return False

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
