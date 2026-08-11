import unittest

from vox.capabilities.ai.llm.models import SanitizedPrompt
from vox.capabilities.ai.llm.router import ComplexityClass, Router


class TestRouter(unittest.TestCase):
    def setUp(self):
        self.router = Router(
            local_model="llama3.2:3b",
            cloud_model="gpt-4o",
            complexity_threshold=512,
        )

    def _prompt(self, text: str, truncated: bool = False) -> SanitizedPrompt:
        return SanitizedPrompt(
            text=text,
            original_length=len(text),
            trimmed_length=len(text),
            tokens_saved=0,
            truncated=truncated,
        )

    def test_simple_prompt_routes_local(self):
        decision = self.router.decide(self._prompt("Hello"))
        self.assertEqual(decision.backend, ComplexityClass.SIMPLE)
        self.assertEqual(decision.model, "llama3.2:3b")

    def test_long_prompt_routes_cloud(self):
        decision = self.router.decide(self._prompt("A" * 600))
        self.assertEqual(decision.backend, ComplexityClass.COMPLEX)
        self.assertEqual(decision.model, "gpt-4o")

    def test_complex_keywords_routes_cloud(self):
        decision = self.router.decide(
            self._prompt("analyze and compare these two algorithms")
        )
        self.assertEqual(decision.backend, ComplexityClass.COMPLEX)

    def test_single_keyword_stays_local(self):
        decision = self.router.decide(self._prompt("explain this concept"))
        self.assertEqual(decision.backend, ComplexityClass.SIMPLE)

    def test_force_cloud(self):
        router = Router(force_cloud=True)
        decision = router.decide(self._prompt("hi"))
        self.assertEqual(decision.backend, ComplexityClass.COMPLEX)
        self.assertEqual(decision.reason, "forced to cloud")

    def test_force_local(self):
        router = Router(force_local=True)
        decision = router.decide(self._prompt("A" * 1000))
        self.assertEqual(decision.backend, ComplexityClass.SIMPLE)
        self.assertEqual(decision.reason, "forced to local")

    def test_system_prompt_contributes_to_length(self):
        decision = self.router.decide(
            self._prompt("hi"),
            system="A" * 600,
        )
        self.assertEqual(decision.backend, ComplexityClass.COMPLEX)

    def test_routing_decision_fields(self):
        decision = self.router.decide(self._prompt("test"))
        self.assertEqual(decision.backend, ComplexityClass.SIMPLE)
        self.assertEqual(decision.reason, "task is simple")
        self.assertEqual(decision.model, "llama3.2:3b")
