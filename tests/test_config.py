import os
import unittest
from unittest.mock import patch, MagicMock

from vox.config.models import VOXConfig, DEFAULT_LOG_PATH, DEFAULT_UDS_PATH
from vox.config.resolver import resolve_config
from vox.config.from_cli import VOXCliArgs
from vox.config.from_env import VOXEnvConfig


class TestVOXConfig(unittest.TestCase):

    def test_defaults(self):
        self.assertEqual(DEFAULT_LOG_PATH, "logs/vox.log")
        self.assertEqual(DEFAULT_UDS_PATH, "/tmp/vox.sock")

    def test_config_dataclass(self):
        cfg = VOXConfig(
            verbose_logging=True,
            log_path="/tmp/test.log",
            uds_path="/tmp/test.sock",
            war_room_id="-123456789",
        )
        self.assertTrue(cfg.verbose_logging)
        self.assertEqual(cfg.log_path, "/tmp/test.log")
        self.assertEqual(cfg.uds_path, "/tmp/test.sock")
        self.assertEqual(cfg.war_room_id, "-123456789")


class TestResolveConfig(unittest.TestCase):

    def test_resolve_with_env(self):
        cli = VOXCliArgs(command=None, agent_name=None)
        env = VOXEnvConfig(war_room_id="-1001234567890")
        cfg = resolve_config(cli, env)
        self.assertFalse(cfg.verbose_logging)
        self.assertEqual(cfg.log_path, DEFAULT_LOG_PATH)
        self.assertEqual(cfg.uds_path, DEFAULT_UDS_PATH)
        self.assertEqual(cfg.war_room_id, "-1001234567890")

    def test_resolve_raises_without_war_room(self):
        cli = VOXCliArgs(command=None, agent_name=None)
        env = VOXEnvConfig(war_room_id="")
        with self.assertRaises(ValueError):
            resolve_config(cli, env)

    def test_cli_verbose_overrides_default(self):
        cli = VOXCliArgs(command=None, agent_name=None, verbose=True)
        env = VOXEnvConfig(war_room_id="-100")
        cfg = resolve_config(cli, env)
        self.assertTrue(cfg.verbose_logging)

    def test_env_verbose_used_when_cli_not_set(self):
        cli = VOXCliArgs(command=None, agent_name=None)
        env = VOXEnvConfig(war_room_id="-100", verbose_logging=True)
        cfg = resolve_config(cli, env)
        self.assertTrue(cfg.verbose_logging)

    def test_cli_verbose_overrides_env(self):
        cli = VOXCliArgs(command=None, agent_name=None, verbose=False)
        env = VOXEnvConfig(war_room_id="-100", verbose_logging=True)
        cfg = resolve_config(cli, env)
        self.assertFalse(cfg.verbose_logging)

    def test_default_verbose_when_none_set(self):
        cli = VOXCliArgs(command=None, agent_name=None)
        env = VOXEnvConfig(war_room_id="-100")
        cfg = resolve_config(cli, env)
        self.assertFalse(cfg.verbose_logging)


class TestLoadEnvConfig(unittest.TestCase):

    @patch("vox.config.from_env.dotenv_values", return_value={})
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_env_var_returns_none(self, mock_dotenv):
        from vox.config.from_env import load_env_config
        cfg = load_env_config()
        self.assertIsNone(cfg.war_room_id)

    @patch("vox.config.from_env.dotenv_values", return_value={"VOX_WAR_ROOM_ID": "-100"})
    @patch.dict(os.environ, {}, clear=True)
    def test_reads_from_dotenv(self, mock_dotenv):
        from vox.config.from_env import load_env_config
        cfg = load_env_config()
        self.assertEqual(cfg.war_room_id, "-100")

    @patch("vox.config.from_env.dotenv_values", return_value={"VOX_WAR_ROOM_ID": "-100"})
    @patch.dict(os.environ, {"VOX_WAR_ROOM_ID": "-200"})
    def test_environ_overrides_dotenv(self, mock_dotenv):
        from vox.config.from_env import load_env_config
        cfg = load_env_config()
        self.assertEqual(cfg.war_room_id, "-200")

    @patch("vox.config.from_env.dotenv_values", return_value={})
    @patch.dict(os.environ, {}, clear=True)
    def test_verbose_logging_default_false(self, mock_dotenv):
        from vox.config.from_env import load_env_config
        cfg = load_env_config()
        self.assertFalse(cfg.verbose_logging)

    @patch("vox.config.from_env.dotenv_values", return_value={"VOX_VERBOSE_LOGGING": "true"})
    @patch.dict(os.environ, {}, clear=True)
    def test_reads_verbose_from_dotenv(self, mock_dotenv):
        from vox.config.from_env import load_env_config
        cfg = load_env_config()
        self.assertTrue(cfg.verbose_logging)

    @patch("vox.config.from_env.dotenv_values", return_value={})
    @patch.dict(os.environ, {"VOX_VERBOSE_LOGGING": "1"})
    def test_verbose_logging_from_environ(self, mock_dotenv):
        from vox.config.from_env import load_env_config
        cfg = load_env_config()
        self.assertTrue(cfg.verbose_logging)

    @patch("vox.config.from_env.dotenv_values", return_value={"VOX_VERBOSE_LOGGING": "false"})
    @patch.dict(os.environ, {"VOX_VERBOSE_LOGGING": "true"})
    def test_environ_overrides_dotenv_verbose(self, mock_dotenv):
        from vox.config.from_env import load_env_config
        cfg = load_env_config()
        self.assertTrue(cfg.verbose_logging)


class TestLoadCliArgs(unittest.TestCase):

    def test_no_args(self):
        with patch("sys.argv", ["vox"]):
            from vox.config.from_cli import load_cli_args
            args = load_cli_args()
            self.assertIsNone(args.command)
            self.assertIsNone(args.agent_name)

    def test_start_command(self):
        with patch("sys.argv", ["vox", "start", "tina"]):
            from vox.config.from_cli import load_cli_args
            args = load_cli_args()
            self.assertEqual(args.command, "start")
            self.assertEqual(args.agent_name, "tina")

    def test_unknown_command_rejected_by_argparse(self):
        with patch("sys.argv", ["vox", "fly"]):
            from vox.config.from_cli import load_cli_args
            with self.assertRaises(SystemExit):
                load_cli_args()

    def test_verbose_flag(self):
        with patch("sys.argv", ["vox", "-v"]):
            from vox.config.from_cli import load_cli_args
            args = load_cli_args()
            self.assertTrue(args.verbose)

    def test_verbose_long_flag(self):
        with patch("sys.argv", ["vox", "--verbose"]):
            from vox.config.from_cli import load_cli_args
            args = load_cli_args()
            self.assertTrue(args.verbose)

    def test_verbose_default_false(self):
        with patch("sys.argv", ["vox"]):
            from vox.config.from_cli import load_cli_args
            args = load_cli_args()
            self.assertFalse(args.verbose)

    def test_verbose_with_command(self):
        with patch("sys.argv", ["vox", "-v", "start", "tina"]):
            from vox.config.from_cli import load_cli_args
            args = load_cli_args()
            self.assertTrue(args.verbose)
            self.assertEqual(args.command, "start")
            self.assertEqual(args.agent_name, "tina")


class TestLoadConfig(unittest.TestCase):

    @patch("vox.config.loader.load_cli_args")
    @patch("vox.config.loader.load_env_config")
    @patch("vox.config.loader.resolve_config")
    def test_load_config_pipeline(
        self, mock_resolve, mock_load_env, mock_load_cli
    ):
        mock_load_cli.return_value = VOXCliArgs(command=None, agent_name=None)
        mock_load_env.return_value = VOXEnvConfig(war_room_id="-100")
        expected = VOXConfig(
            verbose_logging=False,
            log_path=DEFAULT_LOG_PATH,
            uds_path=DEFAULT_UDS_PATH,
            war_room_id="-100",
        )
        mock_resolve.return_value = expected

        from vox.config import load_config
        result = load_config()

        self.assertIs(result, expected)
        mock_load_cli.assert_called_once()
        mock_load_env.assert_called_once()
