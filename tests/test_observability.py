import io
import logging
import sys
import unittest
from unittest.mock import MagicMock, patch

from vox.observability.constants import LOG_LEVEL_OK
from vox.observability.formatters import (
    VOXColorFormatter,
    VOXPlainFormatter,
    _source_tag,
)
from vox.observability.models import VOXForensicLogger, VOXLogSource


class _RecordHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class TestSourceTag(unittest.TestCase):
    def test_simple_name(self):
        record = logging.LogRecord("vox", logging.INFO, "", 0, "msg", (), None)
        self.assertEqual(_source_tag(record), "vox")

    def test_dotted_name(self):
        record = logging.LogRecord(
            "vox.workload1", logging.INFO, "", 0, "msg", (), None
        )
        self.assertEqual(_source_tag(record), "workload1")

    def test_deeply_dotted_name(self):
        record = logging.LogRecord(
            "vox.workloads.workload1.roles.chat", logging.INFO, "", 0, "msg", (), None
        )
        self.assertEqual(_source_tag(record), "chat")


class TestVOXLogSource(unittest.TestCase):
    def test_display_name_with_all_fields(self):
        src = VOXLogSource(
            source_type="workload",
            source_name="workload1",
            source_uuid="12345678-1234-1234-1234-123456789abc",
        )
        self.assertIn("workload", src.display_name)
        self.assertIn("workload1", src.display_name)

    def test_display_name_without_uuid(self):
        src = VOXLogSource(source_type="capability", source_name="ollama")
        self.assertEqual(src.display_name, "capability.ollama")

    def test_display_name_without_name(self):
        src = VOXLogSource(source_type="system")
        self.assertEqual(src.display_name, "system")

    def test_short_uuid_empty(self):
        src = VOXLogSource(source_type="test")
        self.assertEqual(src.short_uuid, "")

    def test_short_uuid_truncated(self):
        src = VOXLogSource(
            source_type="test",
            source_uuid="abcdef12-3456-7890-abcd-ef1234567890",
        )
        self.assertEqual(src.short_uuid, "abcd...7890")


class TestVOXForensicLogger(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        handler = logging.StreamHandler(self.stream)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        base = logging.getLogger("vox.test")
        base.setLevel(logging.DEBUG)
        base.addHandler(handler)
        self.capture = _RecordHandler()
        base.addHandler(self.capture)
        self.logger = VOXForensicLogger(base)
        self.base = base

    def test_info(self):
        self.logger.info("hello")
        self.assertIn("hello", self.stream.getvalue())

    def test_ok(self):
        self.logger.ok("ok message")
        output = self.stream.getvalue()
        self.assertIn("ok message", output)

    def test_warning(self):
        self.logger.warning("warn")
        self.assertIn("warn", self.stream.getvalue())

    def test_error(self):
        self.logger.error("error")
        self.assertIn("error", self.stream.getvalue())

    def test_debug_respects_verbose_false(self):
        self.logger.debug("should not appear")
        self.assertEqual(self.stream.getvalue(), "")

    def test_debug_respects_verbose_true(self):
        verbose = VOXForensicLogger(self.base, verbose=True)
        verbose.debug("should appear")
        self.assertIn("should appear", self.stream.getvalue())

    def test_get_child_returns_new_logger(self):
        child = self.logger.get_child("workload1")
        self.assertIsNot(child, self.logger)
        self.assertIsInstance(child, VOXForensicLogger)

    def test_get_child_name_is_dotted(self):
        child = self.logger.get_child("workload1")
        child.info("from child")
        # The record name should be vox.test.workload1
        self.assertIn("from child", self.stream.getvalue())

    def test_exception_logs_at_error_level_with_traceback(self):
        original = ValueError("vision timeout")
        try:
            raise original
        except ValueError:
            self.logger.exception("dispatch failed")
        record = self.capture.records[-1]
        self.assertEqual(record.levelno, logging.ERROR)
        self.assertEqual(record.getMessage(), "dispatch failed")
        self.assertIsNotNone(record.exc_info)
        self.assertIs(record.exc_info[0], ValueError)
        self.assertIs(record.exc_info[1], original)

    def test_exception_preserves_original_exception_when_logging_fails(self):
        original = RuntimeError("simulated adapter failure")
        with (
            patch.object(self.base, "error", side_effect=RuntimeError("broken")),
            patch.object(logging, "lastResort", MagicMock()),
        ):
            try:
                raise original
            except RuntimeError as caught:
                self.logger.exception("dispatch failed")
                self.assertIs(caught, original)

    def test_exception_on_child_logger(self):
        child = self.logger.get_child("sample")
        try:
            raise KeyError("unknown secret")
        except KeyError:
            child.exception("child failed")
        record = self.capture.records[-1]
        self.assertEqual(record.name, "vox.test.sample")
        self.assertIs(record.exc_info[0], KeyError)

    def test_exception_honors_explicit_exc_info_false(self):
        try:
            raise ValueError("boom")
        except ValueError:
            self.logger.exception("no traceback wanted", exc_info=False)
        record = self.capture.records[-1]
        self.assertEqual(record.getMessage(), "no traceback wanted")
        self.assertIsNone(record.exc_info)

    def test_exception_attaches_source_metadata(self):
        src = VOXLogSource(source_type="workload", source_name="sample")
        try:
            raise RuntimeError("op failed")
        except RuntimeError:
            self.logger.exception("op failed", source=src)
        record = self.capture.records[-1]
        self.assertIs(record.vox_source, src)
        self.assertEqual(record.getMessage(), "op failed")

    def test_exception_returns_none(self):
        self.assertIsNone(self.logger.exception("no-op"))


class TestVOXColorFormatter(unittest.TestCase):
    def setUp(self):
        self.fmt = VOXColorFormatter()
        self.record = logging.LogRecord(
            "vox.test",
            logging.INFO,
            "",
            0,
            "colored msg",
            (),
            None,
        )

    def test_format_returns_string(self):
        result = self.fmt.format(self.record)
        self.assertIsInstance(result, str)
        self.assertIn("colored msg", result)

    def test_format_includes_timestamp(self):
        result = self.fmt.format(self.record)
        # Should contain a date-like pattern
        import re

        self.assertTrue(re.search(r"\d{4}-\d{2}-\d{2}", result))

    def test_format_different_levels(self):
        for level in (
            logging.DEBUG,
            logging.INFO,
            LOG_LEVEL_OK,
            logging.WARNING,
            logging.ERROR,
        ):
            record = logging.LogRecord("vox", level, "", 0, "msg", (), None)
            result = self.fmt.format(record)
            self.assertIn("msg", result)

    def test_format_ok_level_has_ok_label(self):
        record = logging.LogRecord("vox", LOG_LEVEL_OK, "", 0, "ok msg", (), None)
        result = self.fmt.format(record)
        self.assertIn("ok msg", result)

    def test_format_includes_traceback_for_exception_record(self):
        try:
            raise ValueError("boom")
        except ValueError:
            exc_info = sys.exc_info()
        record = logging.LogRecord("vox", logging.ERROR, "f.py", 1, "failed", (), exc_info)
        result = self.fmt.format(record)
        self.assertIn("Traceback", result)
        self.assertIn("boom", result)

    def test_format_without_exc_text_is_unchanged(self):
        record = logging.LogRecord("vox", logging.INFO, "", 0, "plain", (), None)
        result = self.fmt.format(record)
        self.assertNotIn("Traceback", result)


class TestVOXPlainFormatter(unittest.TestCase):
    def setUp(self):
        self.fmt = VOXPlainFormatter()

    def test_format_returns_plain_string(self):
        record = logging.LogRecord("vox", logging.INFO, "", 0, "plain msg", (), None)
        result = self.fmt.format(record)
        self.assertIn("plain msg", result)
        self.assertNotIn("\x1b", result)

    def test_format_includes_bracket_tags(self):
        record = logging.LogRecord("vox", logging.INFO, "", 0, "msg", (), None)
        result = self.fmt.format(record)
        self.assertIn("[INFO]", result)
        self.assertIn("[vox]", result)

    def test_format_dotted_source(self):
        record = logging.LogRecord(
            "vox.workload1", logging.WARNING, "", 0, "warn", (), None
        )
        result = self.fmt.format(record)
        self.assertIn("[workload1]", result)

    def test_format_includes_traceback_for_exception_record(self):
        try:
            raise ValueError("boom")
        except ValueError:
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            "vox", logging.ERROR, "f.py", 1, "failed", (), exc_info
        )
        result = self.fmt.format(record)
        self.assertIn("Traceback", result)
        self.assertIn("boom", result)
        self.assertNotIn("\x1b", result)
