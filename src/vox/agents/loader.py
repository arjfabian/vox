"""AgentLoader — agent configuration and identity manifest loader.

Part of the v0.5.0 State-Isolated Hot-Reload architecture.
Isolates all filesystem and YAML parsing for agent manifests,
environment files, and identity validation away from the agent runtime.
Each call reads fresh from disk to support hot-reload scenarios where
the manifest may have changed.
"""

import yaml
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from vox.observability import VOXForensicLogger


class AgentProvisionError(Exception):
    """Raised when agent configuration or identity validation fails."""
    pass


class AgentLoader:
    """Parses and validates agent configuration from disk.

    Responsible for reading ``agent.yml``, validating against the manifest
    schema, loading the optional ``.env`` file, and verifying that required
    identity fields are present.  Returns a merged config dict consumed by
    the agent runtime.
    """

    MANIFEST_SCHEMA: dict[str, tuple] = {
        "name":                   (str,   True,  "Agent display name"),
        "id":                     (str,   True,  "Unique agent UUID"),
        "master_id":              (str,   False, "Parent agent UUID (empty = root)"),
        "autostart":              (bool,  False, "Start on orchestrator boot"),
        "conversational":         (bool,  False, "Allow free-form LLM conversation"),
        "roles":                  (list,  False, "Explicit role allow-list"),
        "personality":            (dict,  False, "Personality config dict"),
        "rate_limit_max_calls":   (int,   False, "Max events per rate-limit window"),
        "rate_limit_window":      (int,   False, "Rate-limit window in seconds"),
    }

    def __init__(
        self,
        agent_dir: Path,
        logger: VOXForensicLogger,
        global_env: dict[str, Any] | None = None,
    ) -> None:
        self._dir = agent_dir
        self._logger = logger
        self._global_env = global_env or {}

    def load_and_validate(self) -> dict[str, Any]:
        """Load manifest and env, validate identity, return merged config.

        Raises ``AgentProvisionError`` if the manifest is missing or
        identity fields are invalid.
        """
        config: dict[str, Any] = {}

        if not self._load_manifest(config):
            raise AgentProvisionError("Missing or invalid agent.yml")

        self._load_env(config)
        config.update(self._global_env)

        if not self._validate_identity(config, ["name", "id"]):
            raise AgentProvisionError("Invalid agent identity")

        return config

    def _load_manifest(self, config: dict[str, Any]) -> bool:
        manifest_path = self._dir / "agent.yml"
        if not manifest_path.exists():
            self._logger.error(f"Missing agent.yml in {self._dir}")
            return False
        try:
            with open(manifest_path, "r") as f:
                data = yaml.safe_load(f) or {}
            self._validate_manifest(data)
            config.update({
                "name": data.get("name"),
                "id": data.get("id"),
                "master_id": data.get("master_id"),
                "autostart": data.get("autostart", False),
                "roles": data.get("roles", []),
                "personality": data.get("personality", {}),
                "rate_limit_max_calls": data.get("rate_limit_max_calls", 30),
                "rate_limit_window": data.get("rate_limit_window", 60),
            })
            return True
        except AgentProvisionError:
            raise
        except Exception as e:
            self._logger.error(f"Manifest load error: {e}")
            return False

    def _validate_manifest(self, data: dict) -> None:
        errors: list[str] = []
        warnings: list[str] = []
        for field, (expected_type, required, desc) in self.MANIFEST_SCHEMA.items():
            val = data.get(field)
            if val is None:
                if required:
                    errors.append(f"Missing required field '{field}' ({desc})")
                continue
            if not isinstance(val, expected_type):
                errors.append(
                    f"Field '{field}' must be {expected_type.__name__}, "
                    f"got {type(val).__name__}"
                )
        unknown = set(data.keys()) - set(self.MANIFEST_SCHEMA.keys())
        for field in sorted(unknown):
            warnings.append(f"Unknown field '{field}' in agent.yml")
        if errors:
            raise AgentProvisionError(
                f"Manifest validation failed for {self._dir.name}: "
                + "; ".join(errors)
            )
        for w in warnings:
            self._logger.warning(f"[manifest] {w}")

    def _load_env(self, config: dict[str, Any]) -> bool:
        dotenv_path = self._dir / ".env"
        if not dotenv_path.exists():
            return False
        try:
            env_data = dotenv_values(dotenv_path)
            config.update(env_data)
            return True
        except Exception as e:
            self._logger.error(f"Env load error: {e}")
            return False

    @staticmethod
    def _validate_identity(config: dict, required: list[str]) -> bool:
        missing = [k for k in required if not config.get(k)]
        for m in missing:
            pass  # caller (load_and_validate) raises AgentProvisionError
        return len(missing) == 0
