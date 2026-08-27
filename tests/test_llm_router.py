import unittest

from vox.capabilities.ai.llm.models import SanitizedPrompt
from vox.capabilities.ai.llm.router import ComplexityClass, Router


class TestRouter(unittest.TestCase):
    def setUp(self):
        self.router = Router(model="llama3.2:3b")

    def _prompt(self, text: str, truncated: bool = False) -> SanitizedPrompt:
        return SanitizedPrompt(
            text=text,
            original_length=len(text),
            trimmed_length=len(text),
            tokens_saved=0,
            truncated=truncated,
        )

    def test_returns_configured_model(self):
        decision = self.router.decide(self._prompt("Hello"))
        self.assertEqual(decision.tier, ComplexityClass.PRIMARY)
        self.assertEqual(decision.model, "llama3.2:3b")

    def test_always_primary_tier(self):
        decision = self.router.decide(self._prompt("A" * 600))
        self.assertEqual(decision.tier, ComplexityClass.PRIMARY)

    def test_routing_decision_fields(self):
        decision = self.router.decide(self._prompt("test"))
        self.assertEqual(decision.tier, ComplexityClass.PRIMARY)
        self.assertEqual(decision.reason, "resolved model")
        self.assertEqual(decision.model, "llama3.2:3b")

    def test_missing_model_raises(self):
        with self.assertRaises(ValueError) as ctx:
            Router(model="")
        self.assertIn("model is required", str(ctx.exception))
