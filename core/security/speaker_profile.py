"""
core/security/speaker_profile.py

Manages the voice identity of the system operator.
Loads a pre-enrolled embedding from identity/master_voice.npy
and verifies incoming audio against it.

This is system-level infrastructure, not an agent capability.
The Orchestrator owns one instance and exposes it to capabilities
that need speaker verification (e.g. comm.telegram).
"""

import numpy as np
from pathlib import Path
from typing import Optional
from core.logger import log_info, log_ok, log_warn, log_fail

IDENTITY_PATH = Path("identity/master_voice.npy")
SIMILARITY_THRESHOLD = 0.82  # Cosine similarity — tune after enrollment


class SpeakerProfile:
    """
    Wraps the operator's voice embedding.
    Thread-safe for read access (verify is stateless after load).
    """

    def __init__(self):
        self._embedding: Optional[np.ndarray] = None

    @property
    def is_enrolled(self) -> bool:
        return self._embedding is not None

    def load(self, path: Path = IDENTITY_PATH) -> bool:
        """
        Loads the operator embedding from disk.
        Returns True if successful, False if file is missing or corrupt.
        Non-fatal: caller decides whether to abort or degrade gracefully.
        """
        if not path.exists():
            log_warn(
                f"No speaker profile found at '{path}'. "
                "Voice commands disabled. Run: python tools/enroll_speaker.py",
                "security"
            )
            return False

        try:
            self._embedding = np.load(str(path))
            log_ok(f"Speaker profile loaded from '{path}'.", "security")
            return True
        except Exception as e:
            log_fail(f"Failed to load speaker profile: {e}", "security")
            return False

    def verify(self, audio_bytes: bytes) -> bool:
        """
        Compares audio_bytes against the enrolled embedding.
        Returns True if the speaker matches the operator profile.
        Returns False if not enrolled, audio is empty, or similarity is below threshold.
        """
        if not self.is_enrolled:
            log_warn("Speaker verification skipped: no profile loaded.", "security")
            return False

        if not audio_bytes:
            return False

        try:
            from resemblyzer import VoiceEncoder, preprocess_wav
            import io
            import soundfile as sf

            audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes))
            wav = preprocess_wav(audio_data, source_sr=sample_rate)

            encoder = VoiceEncoder()
            embedding = encoder.embed_utterance(wav)

            similarity = float(np.dot(self._embedding, embedding) / (
                np.linalg.norm(self._embedding) * np.linalg.norm(embedding)
            ))

            log_info(f"Speaker similarity score: {similarity:.3f}", "security")
            return similarity >= SIMILARITY_THRESHOLD

        except Exception as e:
            log_fail(f"Speaker verification error: {e}", "security")
            return False