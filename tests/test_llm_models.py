import unittest

from vox.capabilities.ai.llm.models import (
    LLMChatMessage,
    LLMGenerationOptions,
    LLMChatRequest,
    LLMGenerationResult,
    SanitizedPrompt,
)


class TestLLMChatMessage(unittest.TestCase):

    def test_minimal(self):
        msg = LLMChatMessage(role="user", content="hello")
        self.assertEqual(msg.role, "user")
        self.assertEqual(msg.content, "hello")
        self.assertEqual(msg.images, [])

    def test_with_images(self):
        msg = LLMChatMessage(role="user", content="desc", images=["img1.png"])
        self.assertEqual(msg.images, ["img1.png"])


class TestLLMGenerationOptions(unittest.TestCase):

    def test_defaults(self):
        opts = LLMGenerationOptions()
        self.assertEqual(opts.temperature, 0.7)
        self.assertEqual(opts.max_tokens, 2048)

    def test_custom(self):
        opts = LLMGenerationOptions(temperature=0.1, max_tokens=512)
        self.assertEqual(opts.temperature, 0.1)
        self.assertEqual(opts.max_tokens, 512)


class TestLLMChatRequest(unittest.TestCase):

    def test_minimal(self):
        msg = LLMChatMessage(role="user", content="hi")
        req = LLMChatRequest(model="llama3", messages=[msg])
        self.assertEqual(req.model, "llama3")
        self.assertEqual(len(req.messages), 1)
        self.assertFalse(req.stream)
        self.assertIsNone(req.format)
        self.assertIsNone(req.options)

    def test_with_all_fields(self):
        opts = LLMGenerationOptions(temperature=0.5)
        req = LLMChatRequest(
            model="gpt-4",
            messages=[LLMChatMessage(role="user", content="hello")],
            stream=True,
            format="json",
            options=opts,
        )
        self.assertTrue(req.stream)
        self.assertEqual(req.format, "json")
        self.assertIs(req.options, opts)


class TestLLMGenerationResult(unittest.TestCase):

    def test_minimal(self):
        res = LLMGenerationResult(content="response", model="llama3")
        self.assertEqual(res.content, "response")
        self.assertEqual(res.model, "llama3")
        self.assertEqual(res.tokens_generated, 0)
        self.assertIsNone(res.raw)

    def test_full(self):
        res = LLMGenerationResult(
            content="ans", model="gpt-4", tokens_generated=50,
            raw={"usage": {}},
        )
        self.assertEqual(res.tokens_generated, 50)
        self.assertEqual(res.raw, {"usage": {}})


class TestSanitizedPrompt(unittest.TestCase):

    def test_fields(self):
        sp = SanitizedPrompt(
            text="cleaned", original_length=100,
            trimmed_length=80, tokens_saved=5, truncated=False,
        )
        self.assertEqual(sp.text, "cleaned")
        self.assertEqual(sp.original_length, 100)
        self.assertEqual(sp.trimmed_length, 80)
        self.assertEqual(sp.tokens_saved, 5)
        self.assertFalse(sp.truncated)

    def test_truncated_true(self):
        sp = SanitizedPrompt(
            text="short", original_length=1000,
            trimmed_length=500, tokens_saved=125, truncated=True,
        )
        self.assertTrue(sp.truncated)
