"""Config-silo architecture enforcement.

The config silo is the authoritative owner of process/daemon configuration
(``src/vox/config/``). These tests guard the config boundary:

* defaults have single ownership in ``config/models.py`` — no duplicated
  default literals elsewhere;
* raw environment reads for config-owned settings normalize through
  ``from_env.py``, with the deferred UDS-client path (``cli.py``) explicitly
  allowlisted as known NON-CONFORMANT debt;
* the config package imports only its own submodules and stdlib — no reverse
  or out-of-silo package imports (config must not depend on workload,
  orchestration, capability, or runtime internals).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import ClassVar

from vox.config.models import DEFAULT_UDS_PATH

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
CONFIG = SRC / "vox" / "config"


class TestDefaultSingleOwnership(unittest.TestCase):
    """Configuration defaults live only in config/models.py."""

    def test_uds_default_literal_lives_only_in_models(self):
        # The '/tmp/vox.sock' literal must appear only in config/models.py —
        # other modules import DEFAULT_UDS_PATH rather than re-declaring it.
        literal = re.compile(re.escape(DEFAULT_UDS_PATH))
        violations = []
        for py in SRC.glob("vox/**/*.py"):
            if py == CONFIG / "models.py":
                continue
            if literal.search(py.read_text()):
                violations.append(str(py))
        self.assertEqual(violations, [])

    def test_log_path_default_literal_lives_only_in_models(self):
        literal = re.compile(re.escape("logs/vox.log"))
        violations = []
        for py in SRC.glob("vox/**/*.py"):
            if py == CONFIG / "models.py":
                continue
            if literal.search(py.read_text()):
                violations.append(str(py))
        self.assertEqual(violations, [])


class TestEnvReadsAreCentralized(unittest.TestCase):
    """Raw config env reads normalize through from_env.py.

    The UDS client read in ``cli.py`` is known NON-CONFORMANT/deferred debt and
    is explicitly allowlisted so it does not silently reproduced elsewhere.
    """

    # (module, var) pairs that legitimately read config env vars. from_env.py is
    # the owner; cli.py's UDS client path is the deferred NON-CONFORMANT debt.
    ALLOWED_ENV_READS: ClassVar[set[str]] = {
        "vox/config/from_env.py",
        "vox/cli.py",  # deferred NON-CONFORMANT UDS client path — config/AGENTS.md
    }

    def test_config_env_vars_read_only_in_owner_or_allowlisted(self):
        # Only flag actual source reads (os.environ / dotenv_values), not mere
        # text mentions (e.g. resolver error messages naming the environment
        # variable).
        read = re.compile(r"os\.environ|environ\.get|dotenv_values")
        env_vars = ("VOX_WAR_ROOM_ID", "VOX_VERBOSE_LOGGING", "VOX_UDS_PATH")
        violations = []
        for py in SRC.glob("vox/**/*.py"):
            rel = str(py.relative_to(SRC))
            if rel in self.ALLOWED_ENV_READS:
                continue
            for line in py.read_text().splitlines():
                if not read.search(line):
                    continue
                for var in env_vars:
                    if var in line:
                        violations.append((rel, var, line.strip()))
        self.assertEqual(violations, [])


class TestConfigNoOutOfSiloImports(unittest.TestCase):
    """The config package depends only on itself, the stdlib, and dotenv."""

    def test_config_imports_only_stdlib_and_own_submodules(self):
        forbidden = re.compile(r"\bfrom\s+vox\.(?!config)\b|\bimport\s+vox\.(?!config)\b")
        violations = []
        for py in CONFIG.glob("*.py"):
            for lineno, line in enumerate(py.read_text().splitlines(), 1):
                if forbidden.search(line):
                    violations.append(f"{py}:{lineno}: {line.strip()}")
        self.assertEqual(violations, [])


class TestResolverOwnsConstructionAndValidation(unittest.TestCase):
    """Only the resolver constructs/resolves the final VOXConfig.

    Input producers (from_cli/from_env) and the loader must source or compose
    via the resolver, not build a resolved VOXConfig themselves or apply
    precedence.
    """

    def test_voxconfig_is_built_in_resolver_not_in_sources(self):
        construct = re.compile(r"VOXConfig\(")
        # resolver.py constructs VOXConfig; models.py defines it.
        allowed = {CONFIG / "resolver.py", CONFIG / "models.py"}
        violations = []
        for py in CONFIG.glob("*.py"):
            if py in allowed:
                continue
            if construct.search(py.read_text()):
                violations.append(str(py))
        self.assertEqual(violations, [])

    def test_required_env_validation_raises_in_resolver(self):
        resolver = (CONFIG / "resolver.py").read_text()
        self.assertIn("VOX_WAR_ROOM_ID", resolver)
        self.assertIn("ValueError", resolver)

    def test_precedence_documented_only_in_resolver(self):
        resolver = (CONFIG / "resolver.py").read_text()
        loader = (CONFIG / "loader.py").read_text()
        self.assertIn("env_config.verbose_logging", resolver)
        self.assertIn("cli_args.verbose", resolver)
        # loader delegates; it must not apply its own precedence.
        self.assertNotIn("env_config.verbose_logging", loader)
        self.assertNotIn("cli_args.verbose", loader)


class TestConfigModelIsProcessOnly(unittest.TestCase):
    """VOXConfig fields are process/daemon scoped, not workload/capability."""

    def test_voxconfig_is_the_sole_resolved_config_model(self):
        # No other package may declare a concurrent resolved process config.
        duplicate = re.compile(r"class\s+VOXConfig\b")
        hits = []
        for py in SRC.glob("vox/**/*.py"):
            if py == CONFIG / "models.py":
                continue
            if duplicate.search(py.read_text()):
                hits.append(str(py))
        self.assertEqual(hits, [])