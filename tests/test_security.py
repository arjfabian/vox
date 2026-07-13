import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from vox.security.speaker_profile import VOXSpeakerProfile


class TestVOXSpeakerProfile(unittest.TestCase):

    def setUp(self):
        self.logger = MagicMock()
        self.profile = VOXSpeakerProfile(
            identity_dir="/fake/identity",
            logger=self.logger,
        )
        self._orig_resemblyzer = sys.modules.get("resemblyzer")
        mock_resemblyzer = MagicMock()
        mock_resemblyzer.VoiceEncoder = MagicMock()
        mock_resemblyzer.preprocess_wav = MagicMock()
        sys.modules["resemblyzer"] = mock_resemblyzer

        self._orig_soundfile = sys.modules.get("soundfile")
        mock_soundfile = MagicMock()
        sys.modules["soundfile"] = mock_soundfile

    def tearDown(self):
        if self._orig_resemblyzer is not None:
            sys.modules["resemblyzer"] = self._orig_resemblyzer
        else:
            sys.modules.pop("resemblyzer", None)
        if self._orig_soundfile is not None:
            sys.modules["soundfile"] = self._orig_soundfile
        else:
            sys.modules.pop("soundfile", None)

    def test_not_enrolled_by_default(self):
        self.assertFalse(self.profile.is_enrolled)

    def test_enrolled_after_successful_load(self):
        with patch.object(self.profile, "_identity_dir") as mock_dir:
            mock_dir.__truediv__.return_value.exists.return_value = True
            with patch("numpy.load", return_value=np.array([0.1, 0.2, 0.3])):
                result = self.profile.load()
                self.assertTrue(result)
                self.assertTrue(self.profile.is_enrolled)

    def test_load_returns_false_if_file_missing(self):
        with patch.object(self.profile, "_identity_dir") as mock_dir:
            mock_dir.__truediv__.return_value.exists.return_value = False
            result = self.profile.load()
            self.assertFalse(result)
            self.assertFalse(self.profile.is_enrolled)

    def test_load_returns_false_on_corrupt_file(self):
        with patch.object(self.profile, "_identity_dir") as mock_dir:
            mock_dir.__truediv__.return_value.exists.return_value = True
            with patch("numpy.load", side_effect=Exception("corrupt")):
                result = self.profile.load()
                self.assertFalse(result)
                self.assertFalse(self.profile.is_enrolled)

    def test_verify_returns_false_when_not_enrolled(self):
        result = self.profile.verify(b"some audio bytes")
        self.assertFalse(result)

    def test_verify_returns_false_when_no_audio(self):
        self.profile._embedding = np.array([0.1, 0.2, 0.3])
        result = self.profile.verify(b"")
        self.assertFalse(result)

    def test_verify_above_threshold(self):
        resemblyzer = sys.modules["resemblyzer"]
        resemblyzer.VoiceEncoder.return_value.embed_utterance.return_value = (
            np.array([0.5, 0.5])
        )
        resemblyzer.preprocess_wav.return_value = np.array([0.1, 0.2])
        with patch("soundfile.read", return_value=(np.array([0.1, 0.2]), 16000)):
            self.profile._embedding = np.array([0.5, 0.5])
            result = self.profile.verify(b"real audio bytes")
            self.assertTrue(result)

    def test_verify_below_threshold(self):
        resemblyzer = sys.modules["resemblyzer"]
        resemblyzer.VoiceEncoder.return_value.embed_utterance.return_value = (
            np.array([1.0, 0.0])
        )
        resemblyzer.preprocess_wav.return_value = np.array([0.1, 0.2])
        with patch("soundfile.read", return_value=(np.array([0.1, 0.2]), 16000)):
            self.profile._embedding = np.array([0.0, 1.0])
            result = self.profile.verify(b"different audio bytes")
            self.assertFalse(result)

    def test_verify_handles_exception_gracefully(self):
        self.profile._embedding = np.array([0.1, 0.2])
        with patch(
            "soundfile.read",
            side_effect=Exception("audio error"),
        ):
            result = self.profile.verify(b"broken audio")
            self.assertFalse(result)

    def test_cosine_similarity_identical(self):
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([1.0, 0.0, 0.0])
        sim = VOXSpeakerProfile._cosine_similarity(a, b)
        self.assertAlmostEqual(sim, 1.0)

    def test_cosine_similarity_orthogonal(self):
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 1.0])
        sim = VOXSpeakerProfile._cosine_similarity(a, b)
        self.assertAlmostEqual(sim, 0.0)

    def test_cosine_similarity_opposite(self):
        a = np.array([1.0, 0.0])
        b = np.array([-1.0, 0.0])
        sim = VOXSpeakerProfile._cosine_similarity(a, b)
        self.assertAlmostEqual(sim, -1.0)
