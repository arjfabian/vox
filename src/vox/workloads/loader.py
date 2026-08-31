"""WorkloadLoader — workload configuration and persona manifest loader.

Isolates filesystem and YAML parsing for workload manifests and optional
environment files away from the workload runtime.

The loader validates only workload-level configuration. Capability contracts and
capability-specific configuration are intentionally outside its scope.
"""

from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from vox.observability import VOXForensicLogger


class WorkloadProvisionError(Exception):
    """Raised when workload configuration or identity validation fails."""


class WorkloadLoader:
    """Parse and validate workload configuration from disk.

    Responsible for:

      - reading ``manifest.yml``;
      - validating workload-level manifest fields;
      - loading the optional ``.env`` file;
      - applying global environment configuration;
      - validating workload identity.

    Capability-specific configuration is deliberately opaque to the loader.
    The ``overrides`` mapping is passed through for the capability binder to
    validate against individual capability contracts.
    """

    MANIFEST_SCHEMA: dict[str, tuple] = {  # noqa: RUF012
        "name": (str, True, "Display name"),
        "id": (str, True, "Workload UUID"),
        "master_id": (str, False, "Parent workload UUID (empty = root)"),
        "autostart": (bool, False, "Start on orchestrator boot"),
        "conversational": (bool, False, "Allow free-form LLM conversation"),
        "roles": (list, False, "Explicit role allow-list"),
        "personality": (dict, False, "Personality config dict"),
        "overrides": (
            dict,
            False,
            "Per-capability configuration overrides",
        ),
        "rate_limit_max_calls": (
            int,
            False,
            "Max events per rate-limit window",
        ),
        "rate_limit_window": (
            int,
            False,
            "Rate-limit window in seconds",
        ),
    }

    def __init__(
        self,
        persona_dir: Path,
        logger: VOXForensicLogger,
        global_env: dict[str, Any] | None = None,
    ) -> None:
        self._dir = persona_dir
        self._logger = logger
        self._global_env = global_env or {}

    def load_and_validate(self) -> dict[str, Any]:
        """Load manifest and environment, validate identity, return config.

        Raises ``WorkloadProvisionError`` if the manifest is missing, malformed,
        or contains invalid required workload configuration.
        """
        config: dict[str, Any] = {}

        if not self._load_manifest(config):
            raise WorkloadProvisionError("Missing or invalid manifest.yml")

        self._load_env(config)
        config.update(self._global_env)

        if not self._validate_identity(config, ["name", "id"]):
            raise WorkloadProvisionError("Invalid workload identity")

        return config

    def _load_manifest(self, config: dict[str, Any]) -> bool:
        manifest_path = self._dir / "manifest.yml"

        if not manifest_path.exists():
            self._logger.error(f"Missing manifest.yml in {self._dir}")
            return False

        try:
            with open(manifest_path, "r") as f:
                data = yaml.safe_load(f) or {}

            self._validate_manifest(data)

            config.update(
                {
                    "name": data.get("name"),
                    "id": data.get("id"),
                    "master_id": data.get("master_id"),
                    "autostart": data.get("autostart", False),
                    "conversational": data.get("conversational", False),
                    "roles": data.get("roles", []),
                    "personality": data.get("personality", {}),
                    "overrides": data.get("overrides", {}),
                    "rate_limit_max_calls": data.get(
                        "rate_limit_max_calls",
                        30,
                    ),
                    "rate_limit_window": data.get(
                        "rate_limit_window",
                        60,
                    ),
                }
            )

            return True

        except WorkloadProvisionError:
            raise

        except Exception as exc:  # noqa: BLE001
            self._logger.error(f"Manifest load error: {exc}")
            return False

    def _validate_manifest(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise WorkloadProvisionError(
                f"Manifest must contain a mapping: {self._dir / 'manifest.yml'}"
            )

        errors: list[str] = []
        warnings: list[str] = []

        for field, (
            expected_type,
            required,
            description,
        ) in self.MANIFEST_SCHEMA.items():
            value = data.get(field)

            if value is None:
                if required:
                    errors.append(f"Missing required field '{field}' ({description})")
                continue

            if not isinstance(value, expected_type):
                errors.append(
                    f"Field '{field}' must be "
                    f"{expected_type.__name__}, "
                    f"got {type(value).__name__}"
                )

        unknown = set(data) - set(self.MANIFEST_SCHEMA)

        for field in sorted(unknown):
            warnings.append(f"Unknown field '{field}' in manifest.yml")

        if errors:
            raise WorkloadProvisionError(
                f"Manifest validation failed for {self._dir.name}: " + "; ".join(errors)
            )

        for warning in warnings:
            self._logger.warning(f"[manifest] {warning}")

    def _load_env(self, config: dict[str, Any]) -> bool:
        dotenv_path = self._dir / ".env"

        if not dotenv_path.exists():
            return False

        try:
            env_data = dotenv_values(dotenv_path)
            config.update(env_data)
            return True

        except Exception as exc:  # noqa: BLE001
            self._logger.error(f"Env load error: {exc}")
            return False

    @staticmethod
    def _validate_identity(
        config: dict[str, Any],
        required: list[str],
    ) -> bool:
        missing = [key for key in required if not config.get(key)]

        return not missing
