"""Boundary observability: exceptions keep full diagnostics across dispatch edges.

Semantic (not full-snapshot) assertions: dispatch boundaries record the root
operational exception on the forensic logger with its traceback, an adapter
failure is retained through the role-dispatch boundary, a successful lifecycle
transition emits its OK marker, and a broken logging pipeline can never mask
the exception it is reporting.
"""

from __future__ import annotations

import asyncio
import logging
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from vox.observability import VOXForensicLogger
from vox.observability.constants import LOG_LEVEL_OK
from vox.roles import VOXRole
from vox.workloads.base import VOXWorkload
from vox.workloads.lifecycle import WorkloadState


class _RecordHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _manifest(dir_: Path) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "manifest.yml").write_text(
        "name: ObsWorkload\nid: test-uuid-obsbound\nautostart: false\n"
    )


def _raising_role(dir_: Path) -> None:
    (dir_ / "roles").mkdir(parents=True, exist_ok=True)
    (dir_ / "roles" / "boom.py").write_text(
        "from vox.roles import VOXRole\n\n"
        "class Role(VOXRole):\n"
        "    def __init__(self, workload):\n"
        "        super().__init__(workload)\n"
        "        @self.on('parse_receipt')\n"
        "        def _handler(**kwargs):\n"
        "            raise RuntimeError('simulated vision timeout')\n"
    )


class _FailingLLM:
    async def generate_vision(self, **kwargs):
        request = httpx.Request(
            "POST",
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash:generateContent",
        )
        raise httpx.ConnectError("connection refused", request=request)


class _AdapterRole(VOXRole):
    def __init__(self, workload):
        super().__init__(workload)

        @self.on("inbound_message")
        async def _handler(**kwargs):
            await self.workload.capabilities["ai.llm"].generate_vision(
                model="gemini-2.5-flash",
                system="system",
                prompt="describe this image",
                image_bytes=b"\x89PNG-test",
                temperature=0.1,
                max_tokens=16,
                adapter="gemini",
            )


class TestEmitBoundaryPreservesDiagnostics(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp") / f"test_obs_boundaries_{id(self)}"
        self._root = f"vox.obs.emit.{id(self)}"
        self.base = logging.getLogger(self._root)
        self.base.setLevel(logging.DEBUG)
        self.capture = _RecordHandler()
        self.base.addHandler(self.capture)
        self.wl_logger = VOXForensicLogger(self.base)

    def tearDown(self):
        import shutil

        logging.getLogger(self._root).handlers.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _workload(self) -> VOXWorkload:
        _manifest(self.tmp)
        _raising_role(self.tmp)
        orchestrator = MagicMock()
        orchestrator.get_capability_instance.side_effect = lambda cap_id: None
        return VOXWorkload(self.tmp, self.wl_logger, orchestrator)

    def test_emit_failure_logs_root_exception_with_traceback(self):
        wl = self._workload()
        asyncio.run(wl.boot())
        asyncio.run(wl.emit("parse_receipt"))

        failures = [
            r for r in self.capture.records if "Role dispatch failure" in r.msg
        ]
        self.assertEqual(len(failures), 1)
        record = failures[0]
        self.assertIs(record.exc_info[0], RuntimeError)
        self.assertEqual(record.msg, "Role dispatch failure [%s]")
        self.assertEqual(record.args, ("parse_receipt",))

    def test_broken_logger_never_masks_original_dispatch_failure(self):
        wl = self._workload()
        asyncio.run(wl.boot())

        with (
            patch.object(
                wl.logger._logger,
                "error",
                side_effect=RuntimeError("log pipeline down"),
            ),
            patch.object(logging, "lastResort", MagicMock()),
        ):
            # emit must not raise a *new* exception over the operational one,
            # and the dispatch boundary records the root cause regardless.
            asyncio.run(wl.emit("parse_receipt"))


class TestAdapterFailureThroughDispatchBoundary(unittest.TestCase):
    def test_llm_adapter_failure_retains_original_exception(self):
        tmp = Path("/tmp") / f"test_obs_adapter_{id(self)}"
        root = f"vox.obs.adapter.{id(self)}"
        base = logging.getLogger(root)
        base.setLevel(logging.DEBUG)
        capture = _RecordHandler()
        base.addHandler(capture)
        wl_logger = VOXForensicLogger(base)
        try:
            _manifest(tmp)
            orchestrator = MagicMock()
            orchestrator.get_capability_instance.side_effect = lambda cap_id: None
            wl = VOXWorkload(tmp, wl_logger, orchestrator)
            wl.capabilities["ai.llm"] = _FailingLLM()
            role = _AdapterRole(wl)
            wl.roles["adapter_role"] = role
            wl._register_role_routes(role)
            wl._state = WorkloadState.ACTIVE

            asyncio.run(wl.emit("inbound_message"))

            failures = [
                r for r in capture.records if "Role dispatch failure" in r.msg
            ]
            self.assertEqual(len(failures), 1)
            record = failures[0]
            self.assertIs(record.exc_info[0], httpx.ConnectError)
            self.assertEqual(record.msg, "Role dispatch failure [%s]")
            self.assertEqual(record.args, ("inbound_message",))
        finally:
            import shutil

            logging.getLogger(root).handlers.clear()
            shutil.rmtree(tmp, ignore_errors=True)


class TestLifecycleObservability(unittest.TestCase):
    def test_successful_boot_emits_active_ok_marker(self):
        tmp = Path("/tmp") / f"test_obs_lifecycle_{id(self)}"
        root = f"vox.obs.lifecycle.{id(self)}"
        base = logging.getLogger(root)
        base.setLevel(logging.DEBUG)
        capture = _RecordHandler()
        base.addHandler(capture)
        wl_logger = VOXForensicLogger(base)
        try:
            _manifest(tmp)
            _raising_role(tmp)
            orchestrator = MagicMock()
            orchestrator.get_capability_instance.side_effect = lambda cap_id: None
            wl = VOXWorkload(tmp, wl_logger, orchestrator)

            self.assertTrue(asyncio.run(wl.boot()))

            oks = [r for r in capture.records if r.levelno == LOG_LEVEL_OK]
            self.assertTrue(
                any("Workload ACTIVE" in r.getMessage() for r in oks),
                "boot should emit an OK 'Workload ACTIVE' marker",
            )
        finally:
            import shutil

            logging.getLogger(root).handlers.clear()
            shutil.rmtree(tmp, ignore_errors=True)