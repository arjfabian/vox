import unittest

from vox.provider import CapabilityProviderProtocol


class TestCapabilityProviderProtocol(unittest.TestCase):
    def test_is_runtime_checkable(self):

        self.assertTrue(hasattr(CapabilityProviderProtocol, "__instancecheck__"))

    def test_protocol_methods_exist(self):
        methods = [
            "get_capability_instance",
            "get_children",
            "dispatch_inbound_message",
        ]
        for m in methods:
            self.assertTrue(hasattr(CapabilityProviderProtocol, m))

    def test_concrete_class_is_instance(self):
        class FakeProvider:
            def get_capability_instance(self, cap_id: str) -> object | None:
                return None

            def get_children(self, agent_id: str) -> list:
                return []

            async def dispatch_inbound_message(
                self, source: str, payload: dict
            ) -> None:
                pass

        self.assertIsInstance(FakeProvider(), CapabilityProviderProtocol)

    def test_class_missing_method_is_not_instance(self):
        class BadProvider:
            def get_capability_instance(self, cap_id: str) -> object | None:
                return None

        self.assertNotIsInstance(BadProvider(), CapabilityProviderProtocol)
