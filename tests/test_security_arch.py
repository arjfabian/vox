"""Security-silo architecture enforcement.

The security silo (`src/vox/security/`) is a leaf/library domain: it owns the
guardrail, rate-limiter, speaker-profile, and vault primitives plus their
internal state, and imports only ``vox.observability`` (logging). These tests
guard the boundary:

* the security package imports nothing outside its own silo except
  ``vox.observability`` and the stdlib/third-party libs;
* the public package surface exactly matches ``__init__.__all__`` (the exports
  consumers across silos use);
* external silos never reach private security state (``_key``, ``_embedding``,
  ``_calls``, ``_compiled``).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import ClassVar

from vox import security

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
SECURITY = SRC / "vox" / "security"


class TestSecurityLeafImports(unittest.TestCase):
    """Security imports only its own modules, observability, and 3rd-party libs."""

    def test_no_out_of_silo_vox_imports(self):
        forbidden = re.compile(
            r"\bfrom\s+vox\.(?!security|observability)\b"
            r"|\bimport\s+vox\.(?!security|observability)\b"
        )
        violations = []
        for py in SECURITY.glob("*.py"):
            for lineno, line in enumerate(py.read_text().splitlines(), 1):
                if forbidden.search(line):
                    violations.append(f"{py}:{lineno}: {line.strip()}")
        self.assertEqual(violations, [])


class TestPublicSurface(unittest.TestCase):
    """`__all__` is the normative public surface; nothing leaks out-of-band."""

    def test_all_matches_exports(self):
        declared = set(security.__all__)
        for name in declared:
            self.assertTrue(
                hasattr(security, name), f"__all__ names missing module attr: {name}"
            )
            self.assertIn(name, security.__dict__)

    def test_public_contracts_are_exported(self):
        # Primitives other silos consume must be importable from the package root.
        for name in (
            "InputSanitizer",
            "SecurityError",
            "RateLimiter",
            "RateLimitError",
            "VOXSpeakerProfile",
            "VaultAccessError",
            "WorkloadVault",
        ):
            self.assertIn(name, security.__all__, f"missing public export: {name}")

    def test_no_deep_import_of_public_vault_symbols(self):
        # Consumers must import Vault symbols from the package root
        # (``vox.security``), never deep from ``vox.security.vault``.
        deep = re.compile(r"from\s+vox\.security\.vault\s+import\b")
        for py in SRC.glob("vox/**/*.py"):
            if str(py.relative_to(SRC)).startswith("vox/security/"):
                continue
            for lineno, line in enumerate(py.read_text().splitlines(), 1):
                if deep.search(line):
                    self.fail(f"{py}:{lineno}: {line.strip()}")


class TestNoExternalPrivateStateAccess(unittest.TestCase):
    """Security internal state is mutated only inside the security silo."""

    # Private security-state attributes, plus the module owning them.
    SECURITY_PRIVATES: ClassVar[list[str]] = [
        "_key",  # WorkloadVault key material
        "_embedding",  # VOXSpeakerProfile
        "_calls",  # RateLimiter
        "_compiled",  # InputSanitizer
    ]

    def test_private_attrs_only_touched_inside_security(self):
        pattern = re.compile(r"\._(?:key|embedding|calls|compiled)\b")
        violations = []
        for py in SRC.glob("vox/**/*.py"):
            rel = str(py.relative_to(SRC))
            if rel.startswith("vox/security/"):
                continue
            for lineno, line in enumerate(py.read_text().splitlines(), 1):
                # Skip prose/docstring lines (backtick-quoted) — they name the
                # privates to warn against, they do not access them.
                if "`" in line:
                    continue
                if pattern.search(line):
                    violations.append(f"{rel}:{lineno}: {line.strip()}")
        self.assertEqual(violations, [])

    def test_api_server_no_longer_reaches_orchestrator_private_guardrail(self):
        # The previously allowlisted NON-CONFORMANT reach into the orchestrator's
        # private guardrail (``self._orc._guardrail.sanitize``) has been closed:
        # the API server now builds its own ``InputSanitizer`` from ``vox.security``.
        api_server = (SRC / "vox" / "api_server.py").read_text()
        self.assertNotIn("_orc._guardrail", api_server)
        self.assertIn(
            "InputSanitizer", (SRC / "vox" / "api_server.py").read_text()
        )


class TestVaultSeparateStorageFromConfig(unittest.TestCase):
    """WorkloadVault is independent of the config silo; no config coupling."""

    def test_vault_does_not_import_config(self):
        vault = (SECURITY / "vault.py").read_text()
        self.assertNotIn("vox.config", vault)
        self.assertNotIn("VOXConfig", vault)


class TestSpeakerIdentityIsPath(unittest.TestCase):
    """Speaker identity dir is a Path (used with path division at runtime)."""

    def test_speaker_identity_dir_typed_as_path(self):
        speaker = (SECURITY / "speaker_profile.py").read_text()
        self.assertIn("from pathlib import Path", speaker)
        self.assertIn("identity_dir: Path", speaker)