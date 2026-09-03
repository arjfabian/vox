"""Services-silo architecture enforcement.

`vox.services` is a **leaf service package** owning the `FleetMessenger` outward
transport adapter. It is distinct from `vox.messaging` (the message-model
contract silo): services does transport, messaging does representation, and the
two must not become coupled. These tests guard:

* the services package imports nothing from the rest of VOX except `vox.observability`;
* `FleetMessenger` never references orchestration/workload internals or
  `vox.messaging` models;
* the public surface is exactly `FleetMessenger`;
* the secret `bot_token` is never logged/serialized.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from vox import services

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
SVC = SRC / "vox" / "services"


class TestServicesLeafImports(unittest.TestCase):
    """Services imports only vox.observability (plus httpx/stdlib)."""

    def test_no_out_of_silo_imports(self):
        forbidden = re.compile(
            r"\bfrom\s+vox\.(?!observability)\w+\b"
            r"|\bimport\s+vox\.(?!observability)\w+\b"
        )
        violations = []
        for py in SVC.glob("*.py"):
            if py.name == "__init__.py":
                continue
            for lineno, line in enumerate(py.read_text().splitlines(), 1):
                if forbidden.search(line):
                    violations.append(f"{py}:{lineno}: {line.strip()}")
        self.assertEqual(violations, [])


class TestFleetMessengerBoundary(unittest.TestCase):
    """FleetMessenger is a transport adapter, not orchestration/workload code."""

    def test_no_orchestration_or_workload_reach(self):
        fm = (SVC / "fleet_messenger.py").read_text()
        self.assertNotIn("orchestration", fm)
        self.assertNotIn("workload", fm)
        self.assertNotIn("vox.messaging", fm)
        self.assertNotIn("VOXWorkload", fm)
        self.assertNotIn("VOXOrchestrator", fm)

    def test_no_messaging_models(self):
        fm = (SVC / "fleet_messenger.py").read_text()
        self.assertNotIn("VOXMessage", fm)
        self.assertNotIn("MSG_", fm)

    def test_public_surface_is_fleet_messenger(self):
        self.assertEqual(services.__all__, ["FleetMessenger"])
        self.assertTrue(hasattr(services, "FleetMessenger"))


class TestSecretHandling(unittest.TestCase):
    """The bot token must never be logged, serialized, or dumped."""

    def test_token_not_logged_or_serialized(self):
        fm = (SVC / "fleet_messenger.py").read_text()
        # Any line that references the token alongside a logger call,
        # serialization, or print would be a leak.
        for lineno, line in enumerate(fm.splitlines(), 1):
            if "_bot_token" in line and any(
                marker in line for marker in ("logger", "json.dumps", "print")
            ):
                self.fail(f"fleet_messenger.py:{lineno}: {line.strip()}")

    def test_token_reaches_api_only_via_base_url(self):
        fm = (SVC / "fleet_messenger.py").read_text()
        self.assertIn("api.telegram.org/bot{self._bot_token}", fm)


class TestSingleConstructionSite(unittest.TestCase):
    """FleetMessenger is constructed only by orchestration bootstrap."""

    def test_constructed_only_in_orchestration_bootstrap(self):
        hits = []
        construct = re.compile(r"FleetMessenger\(")
        for py in SRC.glob("vox/**/*.py"):
            rel = str(py.relative_to(SRC))
            if rel.startswith("vox/services/"):
                continue
            if construct.search(py.read_text()):
                hits.append(rel)
        self.assertEqual(hits, ["vox/orchestration/base.py"])