"""Observability-silo architecture enforcement.

Observability is a **leaf service domain** (`src/vox/observability/`): it owns
the logging models, formatters, and the ``LOG_LEVEL_OK`` constant, and imports
only stdlib and its own submodules. These tests guard the boundary:

* the observability package imports nothing from the rest of the codebase;
* logger construction is owned by a single bootstrap site (`cli.py`), which
  wires resolved config intent (log path, verbose) into the logger;
* the consumed ``LOG_LEVEL_OK`` constant is exposed from the package root;
* external silos never reach ``VOXForensicLogger._logger`` or formatter
  internals.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from vox import observability

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
OBS = SRC / "vox" / "observability"


class TestObservabilityLeafImports(unittest.TestCase):
    """Observability imports only stdlib and its own submodules."""

    def test_no_out_of_silo_imports(self):
        # No import of any other vox.* package (config, workloads, runtime, ...).
        # Intra-silo absolute imports (vox.observability.*) are allowed.
        forbidden = re.compile(
            r"\bfrom\s+vox\.(?!observability)\w+\b"
            r"|\bimport\s+vox\.(?!observability)\w+\b"
        )
        violations = []
        for py in OBS.glob("*.py"):
            for lineno, line in enumerate(py.read_text().splitlines(), 1):
                if forbidden.search(line):
                    violations.append(f"{py}:{lineno}: {line.strip()}")
        self.assertEqual(violations, [])


class TestPublicSurface(unittest.TestCase):
    """Everything consumed cross-silo is exported from the package root."""

    def test_consumed_constant_is_public(self):
        # LOG_LEVEL_OK is consumed by comm.gateway; it must be on the public
        # surface, not only reachable via a deep path.
        self.assertIn("LOG_LEVEL_OK", observability.__all__)
        self.assertEqual(observability.LOG_LEVEL_OK, 25)

    def test_public_contracts_are_exported(self):
        for name in (
            "VOXForensicLogger",
            "VOXLogSource",
            "VOXColorFormatter",
            "VOXPlainFormatter",
            "LOG_LEVEL_OK",
        ):
            self.assertIn(name, observability.__all__, f"missing export: {name}")
            self.assertTrue(hasattr(observability, name))


class TestSingleLoggerConstructionSite(unittest.TestCase):
    """The logger/formatter assembly lives in the cli bootstrap, not elsewhere."""

    def test_forensic_logger_constructed_only_in_cli(self):
        # External construction of VOXForensicLogger must happen only at the
        # bootstrap (cli.py). Internal construction via get_child is fine.
        pattern = re.compile(r"VOXForensicLogger\(")
        hits = []
        for py in SRC.glob("vox/**/*.py"):
            if py.name.startswith("__init__"):
                continue
            if str(py.relative_to(SRC)) == "vox/cli.py":
                continue
            rel = str(py.relative_to(SRC))
            if rel.startswith("vox/observability/"):
                continue
            if pattern.search(py.read_text()):
                hits.append(rel)
        self.assertEqual(hits, [])

    def test_handler_and_formatter_attach_only_in_cli(self):
        # Only the bootstrap attaches handlers or wires the observability
        # formatters to the 'vox' hierarchy.
        attach = re.compile(r"setFormatter\(VOX|addHandler\(|StreamHandler|FileHandler")
        for py in SRC.glob("vox/**/*.py"):
            rel = str(py.relative_to(SRC))
            if rel == "vox/cli.py":
                continue
            if rel.startswith("vox/observability/"):
                continue
            for lineno, line in enumerate(py.read_text().splitlines(), 1):
                if attach.search(line):
                    self.fail(f"{rel}:{lineno}: {line.strip()}")


class TestVerboseWiring(unittest.TestCase):
    """Bootstrap wires resolved verbose intent into the forensic logger."""

    def test_cli_constructs_logger_with_verbose_from_config(self):
        cli = (SRC / "vox" / "cli.py").read_text()
        self.assertIn(
            "VOXForensicLogger(base_logger, verbose=config.verbose_logging)",
            cli,
        )

    def test_verbose_propagates_through_get_child(self):
        models = (OBS / "models.py").read_text()
        self.assertIn("verbose=self.verbose", models)


class TestNoPrivateObservabilityAccess(unittest.TestCase):
    """External silos use the public surface, not submodule deep paths."""

    def test_no_deep_package_import_of_observability_by_consumers(self):
        # Consumer code should import from the package root, not the submodule
        # path. The comm.gateway server's deep import of LOG_LEVEL_OK is known
        # NON-CONFORMANT debt now resolvable via the public surface; new deep
        # imports are prohibited.
        deep = re.compile(r"from vox\.observability\.(formatters|models) import")
        deep_constants = re.compile(
            r"from vox\.observability\.constants import"
        )
        for py in SRC.glob("vox/**/*.py"):
            rel = str(py.relative_to(SRC))
            if rel.startswith("vox/observability/"):
                continue
            content = py.read_text()
            if rel == "vox/capabilities/comm/gateway/server.py":
                # Known NON-CONFORMANT: deep-imports LOG_LEVEL_OK, now available
                # on the public surface. Switching the import is a capability-
                # silo change and is deferred.
                content = content.replace(
                    "from vox.observability.constants import LOG_LEVEL_OK",
                    "",
                )
            for lineno, line in enumerate(content.splitlines(), 1):
                if deep.search(line) or deep_constants.search(line):
                    self.fail(f"{rel}:{lineno}: {line.strip()}")