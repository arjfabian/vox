"""API-server architecture enforcement.

The API server is a process-facing **transport/control boundary** over the
orchestrator's public fleet operations — not a second orchestration or security
layer. It parses requests, maps them to public orchestrator operations, and
serializes responses; it must never reach orchestration/security/runtime
**privates** or duplicate their owned policy/state.

These tests guard the boundary:

* no private orchestration access (``_orc.*``, ``_resolve_workload``,
  ``_guardrail`` reach into orchestrator state);
* no private security access and no duplication of security policy in handlers —
  the HTTP ingress guardrail is applied through the public ``vox.security``
  ``InputSanitizer`` that the API server constructs for its own ingress;
* handlers invoke fleet operations through the public orchestrator surface only;
* no runtime/config ownership duplication — the API server is composed by the
  runtime and does not re-create config or lifecycle state it does not own;
* lifecycle (``start``/``shutdown``) is driven by the runtime composition root.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "src" / "vox" / "api_server.py"
SRC = ROOT / "src" / "vox"


class TestAPIServerNoPrivateOrchestrationReach(unittest.TestCase):
    """The API server must consume the orchestrator's public surface only."""

    def test_no_private_orchestrator_access(self):
        # Reaching ANY private attribute of the orchestrator (or a private
        # orchestrator method such as the removed `_resolve_workload`) is
        # forbidden; only public `self._orc.<public>` calls are allowed.
        forbidden = re.compile(r"\._orc\._[a-zA-Z]|_orc\._[a-zA-Z]|\._resolve_workload\b")
        api = API.read_text()
        hits = [h for h in forbidden.findall(api) if h]
        self.assertEqual(hits, [])

    def test_no_reach_into_orchestrator_private_guardrail(self):
        api = API.read_text()
        self.assertNotIn("_orc._guardrail", api)
        self.assertNotIn("orc._guardrail", api)

    def test_handlers_use_only_public_fleet_operations(self):
        api = API.read_text()
        for private in ("_resolve_workload(", ".pause(", ".resume(", ".stop("):
            self.assertNotIn(private, api)


class TestAPIServerPublicOrchestrationInteractions(unittest.TestCase):
    """Handlers map requests onto public orchestrator fleet operations."""

    def test_fleet_operations_are_public(self):
        api = API.read_text()
        for public in (
            "get_fleet_snapshot",
            "resolve_workload",
            "resolve_workload_id",
            "pause_workload",
            "resume_workload",
            "stop_workload",
            "start_workload_by_name",
            "restart_workload",
        ):
            self.assertIn(public, api)


class TestAPIServerSecurityBoundary(unittest.TestCase):
    """API server applies the ingress guardrail via the public security surface."""

    def test_guardrail_uses_public_security_input_sanitizer(self):
        api = API.read_text()
        self.assertIn("from vox.security import InputSanitizer", api)
        self.assertIn("InputSanitizer(", api)  # owns an instance for its ingress
        self.assertIn("_guardrail.sanitize(", api)  # applies it itself

    def test_auth_and_guardrail_are_middleware_not_handler_duplicated(self):
        api = API.read_text()
        # Security/auth policy lives in middleware only, never re-implemented
        # per-handler; the guardrail sanitize is invoked exactly once (ingress).
        self.assertEqual(api.count("sanitize("), 1)
        self.assertNotIn("_rate", api)


class TestAPIServerNoRuntimeConfigOwnership(unittest.TestCase):
    """The API server must not duplicate runtime/config ownership."""

    def test_no_local_runtime_config_dataclass(self):
        api = API.read_text()
        self.assertNotIn("VOXAPIConfig", api)
        self.assertNotIn("class VOXConfig", api)

    def test_lifecycle_size_is_bounded_to_transport(self):
        # start/shutdown only operate the aiohttp runner/site; they must not
        # compose orchestrator or runtime and must not own fleet boot.
        api = API.read_text()
        self.assertIn("def start(", api)
        self.assertIn("def shutdown(", api)
        self.assertNotIn("orchestrator.boot", api)
        self.assertNotIn("runtime.orchestrator", api)

    def test_no_import_of_runtime_is_a_second_composition_root(self):
        api = API.read_text()
        self.assertNotIn("vox.runtime", api)
        self.assertNotIn("VOXRuntime", api)


class TestAPIServerObservabilityBoundary(unittest.TestCase):
    """The API server uses the composed logger; it builds no formatters."""

    def test_logs_via_composed_logger_only(self):
        api = API.read_text()
        self.assertIn("logger", api)
        self.assertNotIn("Formatter(", api)
        self.assertNotIn("__import__", api)