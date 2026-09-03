import unittest

from vox.provider import CapabilityProviderProtocol


class TestCapabilityProviderProtocol(unittest.TestCase):
    def test_is_runtime_checkable(self):

        self.assertTrue(hasattr(CapabilityProviderProtocol, "__instancecheck__"))

    def test_protocol_methods_exist(self):
        methods = [
            "get_capability_instance",
            "dispatch_inbound_message",
        ]
        for m in methods:
            self.assertTrue(hasattr(CapabilityProviderProtocol, m))

    def test_concrete_class_is_instance(self):
        class FakeProvider:
            def get_capability_instance(self, cap_id: str) -> object | None:
                return None

            async def dispatch_inbound_message(
                self, source: str, payload: dict
            ) -> None:
                pass

        self.assertIsInstance(FakeProvider(), CapabilityProviderProtocol)

    def test_protocol_no_longer_exposes_fleet_topology(self):
        """get_children was removed: the workload is not a fleet authority and
        hierarchy is reported by orchestration via get_hierarchy_snapshot."""
        self.assertFalse(hasattr(CapabilityProviderProtocol, "get_children"))

    def test_class_missing_method_is_not_instance(self):
        class BadProvider:
            def get_capability_instance(self, cap_id: str) -> object | None:
                return None

        self.assertNotIsInstance(BadProvider(), CapabilityProviderProtocol)
