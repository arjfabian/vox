"""Entry-point / package-surface architecture enforcement.

The process entry surface is ``src/vox/__main__.py`` (``python -m vox``),
``src/vox/cli.py`` (the ``vox`` console script ``main()``), and the package
root ``src/vox/__init__.py``. It is the **thin** process bootstrap: it
normalizes raw CLI/env input, delegates config resolution to ``vox.config``,
constructs the logger (the single designated site), and delegates process
composition to ``vox.runtime`` (`build_vox` / `run_vox`). It must not contain
domain logic, re-enter the package root, reach runtime privates, or be imported
by domain silos.

These tests guard the boundary:

* ``__main__`` stays thin — delegates to ``vox.cli.main``, no domain imports;
* entry points depend downward only — no domain silo imports ``vox.cli`` /
  ``vox.__main__``;
* the package root ``__init__`` is a namespace surface — no ``from vox import``
  / ``import vox`` re-entry that could recreate the runtime circular import;
* no runtime-private access from the CLI;
* exactly one logger-construction site (``cli.py``) across the codebase;
* env-read ownership: the entry points read no config env var except the
  documented allowlisted ``VOX_UDS_PATH`` UDS-client path.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "vox"
MAIN = SRC / "__main__.py"
CLI = SRC / "cli.py"
PKG_INIT = SRC / "__init__.py"


class TestMainIsThin(unittest.TestCase):
    """``python -m vox`` must be a trivially thin delegation stub."""

    def test_main_delegates_to_cli_main(self):
        main = MAIN.read_text()
        self.assertIn("from vox.cli import main", main)
        self.assertIn("main()", main)

    def test_main_has_no_domain_imports(self):
        main = MAIN.read_text()
        for domain in ("orchestration", "observability", "security", "runtime"):
            self.assertNotIn(f"from vox.{domain}", main)
            self.assertNotIn(f"import vox.{domain}", main)


class TestCliDelegatesNotReimplements(unittest.TestCase):
    """cli.py wires the process; it does not reimplement owned logic."""

    def test_config_delegated_to_config_silo(self):
        cli = CLI.read_text()
        self.assertIn("from vox.config import load_config", cli)
        self.assertIn("from vox.config.from_cli import load_cli_args", cli)
        self.assertNotIn("parse_args", cli)

    def test_runtime_delegated_to_runtime_silo(self):
        cli = CLI.read_text()
        self.assertIn("from vox.runtime import", cli)
        self.assertIn("build_vox", cli)
        self.assertIn("run_vox", cli)

    def test_no_runtime_private_access(self):
        cli = CLI.read_text()
        self.assertNotIn("runtime._", cli)
        self.assertNotIn("_orc", cli)

    def test_no_domain_reimplementation(self):
        cli = CLI.read_text()
        # CLI must not construct VOXConfig, orchestrators, or security primitives.
        self.assertNotIn("VOXConfig(", cli)
        self.assertNotIn("VOXOrchestrator(", cli)
        self.assertNotIn("InputSanitizer(", cli)


class TestPackageRootIsNamespaceOnly(unittest.TestCase):
    """The package root must not re-export / re-enter the package.

    A non-empty top-level ``__init__`` that re-imports submodules recreates the
    circular-import hazard the runtime already fixed. Submodules must be
    imported through their own leaf namespaces.
    """

    def test_root_init_is_effectively_empty(self):
        init = PKG_INIT.read_text()
        self.assertNotIn("import", init)
        self.assertNotIn("from", init)

    def test_no_leaf_imports_from_package_root(self):
        # `import vox` / `from vox import X` (bare root) must not appear.
        # `import vox` (bare root) must not appear; `import vox.sub` is fine.
        forbidden = re.compile(r"^import\s+vox\s*$|^from\s+vox\s+import\b")
        for py in SRC.rglob("*.py"):
            for lineno, line in enumerate(py.read_text().splitlines(), 1):
                if forbidden.search(line):
                    self.fail(f"{py}:{lineno}: {line.strip()}")


class TestEntryPointDependencyDirection(unittest.TestCase):
    """Domain silos must not depend upward on the entry point."""

    def test_no_domain_silo_imports_cli_or_main(self):
        for py in SRC.rglob("*.py"):
            if py in (CLI, MAIN):
                continue
            text = py.read_text()
            self.assertNotIn("from vox.cli", text, str(py))
            self.assertNotIn("import vox.cli", text, str(py))
            self.assertNotIn("from vox import", text, str(py))


class TestSingleLoggerConstructionSite(unittest.TestCase):
    """Cli is the single logger-construction site (observability contract)."""

    def test_only_cli_constructs_the_forensic_logger(self):
        arrivals = []
        for py in SRC.rglob("*.py"):
            text = py.read_text()
            # Construction = calling `VOXForensicLogger(`; the class body in
            # observability/models.py defines it (class ...), not constructs it.
            if "VOXForensicLogger(" in text and "class VOXForensicLogger" not in text:
                arrivals.append(str(py))
        self.assertEqual(arrivals, [str(CLI)])

    def test_no_logger_handler_construction_outside_cli(self):
        for py in SRC.rglob("*.py"):
            if py == CLI:
                continue
            text = py.read_text()
            if "addHandler" in text or "FileHandler(" in text or "basicConfig(" in text:
                self.fail(f"logger construction outside cli.py: {py}")


class TestEntryPointEnvOwnership(unittest.TestCase):
    """Entry points read no config env var except the allowlisted UDS client."""

    def test_cli_reads_only_uds_path_env(self):
        cli = CLI.read_text()
        # The single documented allowlisted env read: the UDS-client path.
        self.assertIn("VOX_UDS_PATH", cli)
        self.assertNotIn("VOX_API_TOKEN", cli)
        self.assertNotIn("VOX_API_HOST", cli)
        self.assertNotIn("VOX_WAR_ROOM_ID", cli)
        self.assertNotIn("VOX_VERBOSE_LOGGING", cli)

    def test_main_reads_no_env(self):
        self.assertNotIn("os.environ", MAIN.read_text())
        self.assertNotIn("getenv", MAIN.read_text())