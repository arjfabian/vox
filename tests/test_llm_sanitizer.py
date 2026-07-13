import asyncio
import unittest

from vox.capabilities.ai.llm.sanitizer import sanitize, _estimate_tokens, _strip_boilerplate


class TestEstimateTokens(unittest.TestCase):

    def test_empty(self):
        self.assertEqual(_estimate_tokens(""), 1)

    def test_short(self):
        self.assertEqual(_estimate_tokens("hello world"), 2)

    def test_longer(self):
        self.assertEqual(_estimate_tokens("a" * 100), 25)


class TestStripBoilerplate(unittest.TestCase):

    def test_strips_you_are_an_ai_assistant(self):
        result = _strip_boilerplate("You are an AI assistant. Help the user.")
        self.assertEqual(result, "Help the user.")

    def test_strips_you_are_a_helpful_assistant(self):
        result = _strip_boilerplate("You are a helpful assistant. Answer questions.")
        self.assertEqual(result, "Answer questions.")

    def test_case_insensitive(self):
        result = _strip_boilerplate("YOU ARE A LARGE LANGUAGE MODEL. reply.")
        self.assertEqual(result, "reply.")

    def test_no_boilerplate_returns_original(self):
        text = "What is the capital of France?"
        self.assertEqual(_strip_boilerplate(text), text)

    def test_strips_trailing_punctuation(self):
        result = _strip_boilerplate("You are an AI assistant: do this")
        self.assertEqual(result, "do this")


class TestSanitize(unittest.TestCase):

    def _run(self, text: str, max_chars: int = 16384):
        return asyncio.run(sanitize(text, max_input_chars=max_chars))

    def test_control_chars_removed(self):
        result = self._run("hello\x00world\x1f")
        self.assertEqual(result.text, "helloworld")

    def test_multiline_blank_collapsed(self):
        result = self._run("a\n\n\n\nb")
        self.assertEqual(result.text, "a\n\nb")

    def test_boilerplate_stripped(self):
        result = self._run("You are an AI assistant. Hi!")
        self.assertNotIn("You are an AI assistant", result.text)
        self.assertIn("Hi!", result.text)

    def test_truncation(self):
        text = "line1\nline2\nline3\nline4"
        result = self._run(text, max_chars=10)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.text), 10)

    def test_no_truncation_within_limit(self):
        text = "short"
        result = self._run(text, max_chars=100)
        self.assertFalse(result.truncated)
        self.assertEqual(result.text, text)

    def test_preserves_original_length(self):
        text = "Hello, world!"
        result = self._run(text)
        self.assertEqual(result.original_length, len(text))

    def test_tokens_saved_non_negative(self):
        text = "You are an AI assistant. " * 100
        result = self._run(text)
        self.assertGreaterEqual(result.tokens_saved, 0)

    def test_truncation_rsplit_respects_newlines(self):
        text = "aaaaa\nbbbbb\nccccc\nddddd"
        result = self._run(text, max_chars=12)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.text), 12)
