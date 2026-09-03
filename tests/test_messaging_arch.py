"""Messaging-silo architecture enforcement.

`vox.messaging` is a **leaf contract/model package** owning the `VOXMessage`
envelope and the delegation-protocol `MSG_*` constants. It is distinct from
`vox.services` (the transport-adapter silo). These tests guard:

* the messaging package imports no `vox.*` package at all (true leaf);
* the public surface exposes `VOXMessage` and the `MSG_*` constants;
* messaging never couples to `vox.services` (representation vs transport);
* consumers do not introduce new deep imports of messaging submodules.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from vox import messaging

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
MSG = SRC / "vox" / "messaging"


class TestMessagingLeafImports(unittest.TestCase):
    """Messaging imports only stdlib and pydantic — no vox.* at all."""

    def test_no_out_of_silo_vox_imports(self):
        # No import of any OTHER vox.* package. Intra-silo absolute imports
        # (vox.messaging.models) are allowed.
        forbidden = re.compile(r"\bfrom\s+vox\.(?!messaging)\w+\b|\bimport\s+vox\.(?!messaging)\w+\b")
        for py in MSG.glob("*.py"):
            for lineno, line in enumerate(py.read_text().splitlines(), 1):
                if forbidden.search(line):
                    self.fail(f"{py}:{lineno}: {line.strip()}")

    def test_imports_outside_messaging_are_leaf_only(self):
        # Messaging may only depend on stdlib + pydantic, never another silo.
        models = (MSG / "models.py").read_text()
        for name in ("vox.observability", "vox.config", "vox.security",
                     "vox.workloads", "vox.orchestration", "vox.services",
                     "vox.runtime", "vox.capabilities"):
            self.assertNotIn(name, models)


class TestPublicSurface(unittest.TestCase):
    """The envelope and protocol constants are on the public root."""

    def test_vox_message_public(self):
        self.assertIn("VOXMessage", messaging.__all__)
        self.assertTrue(hasattr(messaging, "VOXMessage"))

    def test_protocol_constants_public(self):
        for name in ("MSG_COMMAND_REQUEST", "MSG_ACKNOWLEDGED",
                     "MSG_COMMAND_RESULT", "MSG_COMMAND_ERROR"):
            self.assertIn(name, messaging.__all__, f"missing export: {name}")
            self.assertTrue(hasattr(messaging, name))

    def test_all_names_resolve(self):
        for name in messaging.__all__:
            self.assertTrue(hasattr(messaging, name), f"__all__ missing: {name}")


class TestNoServicesCoupling(unittest.TestCase):
    """Messaging (representation) stays independent of transport (services)."""

    def test_messaging_does_not_import_services(self):
        for py in MSG.glob("*.py"):
            self.assertNotIn("vox.services", py.read_text())
            self.assertNotIn("FleetMessenger", py.read_text())


class TestMessageIsImmutableEnvelope(unittest.TestCase):
    """VOXMessage is the frozen contract; representation, not routing state."""

    def test_model_is_frozen(self):
        models = (MSG / "models.py").read_text()
        self.assertIn('"frozen": True', models)

    def test_model_has_emit_validation(self):
        models = (MSG / "models.py").read_text()
        self.assertIn("validate_iso8601_datetime", models)
        self.assertIn("fromisoformat", models)


class TestNoNewConsumerDeepImports(unittest.TestCase):
    """Consumers use the package root; new deep imports are prohibited."""

    def test_no_deep_submodule_imports_outside_messaging(self):
        deep = re.compile(r"from vox\.messaging\.models import")
        for py in SRC.glob("vox/**/*.py"):
            rel = str(py.relative_to(SRC))
            if rel.startswith("vox/messaging/"):
                continue
            if rel == "vox/orchestration/war_room.py":
                # Known NON-CONFORMANT: deep-imports VOXMessage. It is on the
                # public surface; switching is an orchestration-side change and
                # is deferred. No new deep imports are allowed.
                continue
            for lineno, line in enumerate(py.read_text().splitlines(), 1):
                if deep.search(line):
                    self.fail(f"{rel}:{lineno}: {line.strip()}")