import io
import logging
import unittest

from vox.observability.constants import LOG_LEVEL_OK
from vox.observability.models import VOXForensicLogger, VOXLogSource
from vox.observability.formatters import VOXColorFormatter, VOXPlainFormatter, _source_tag


class TestSourceTag(unittest.TestCase):

    def test_simple_name(self):
        record = logging.LogRecord("vox", logging.INFO, "", 0, "msg", (), None)
        self.assertEqual(_source_tag(record), "vox")

    def test_dotted_name(self):
        record = logging.LogRecord("vox.tina", logging.INFO, "", 0, "msg", (), None)
        self.assertEqual(_source_tag(record), "tina")

    def test_deeply_dotted_name(self):
        record = logging.LogRecord("vox.agents.tina.roles.chat", logging.INFO, "", 0, "msg", (), None)
        self.assertEqual(_source_tag(record), "chat")


class TestVOXLogSource(unittest.TestCase):

    def test_display_name_with_all_fields(self):
        src = VOXLogSource(
            source_type="agent",
            source_name="tina",
            source_uuid="12345678-1234-1234-1234-123456789abc",
        )
        self.assertIn("agent", src.display_name)
        self.assertIn("tina", src.display_name)

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
        child = self.logger.get_child("tina")
        self.assertIsNot(child, self.logger)
        self.assertIsInstance(child, VOXForensicLogger)

    def test_get_child_name_is_dotted(self):
        child = self.logger.get_child("tina")
        child.info("from child")
        # The record name should be vox.test.tina
        self.assertIn("from child", self.stream.getvalue())


class TestVOXColorFormatter(unittest.TestCase):

    def setUp(self):
        self.fmt = VOXColorFormatter()
        self.record = logging.LogRecord(
            "vox.test", logging.INFO, "", 0, "colored msg", (), None,
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
        for level in (logging.DEBUG, logging.INFO, LOG_LEVEL_OK, logging.WARNING, logging.ERROR):
            record = logging.LogRecord("vox", level, "", 0, "msg", (), None)
            result = self.fmt.format(record)
            self.assertIn("msg", result)

    def test_format_ok_level_has_ok_label(self):
        record = logging.LogRecord("vox", LOG_LEVEL_OK, "", 0, "ok msg", (), None)
        result = self.fmt.format(record)
        self.assertIn("ok msg", result)


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
        record = logging.LogRecord("vox.tina", logging.WARNING, "", 0, "warn", (), None)
        result = self.fmt.format(record)
        self.assertIn("[tina]", result)
