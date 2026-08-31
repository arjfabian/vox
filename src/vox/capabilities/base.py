"""Capability system — core abstraction layer.

Defines the interface and runtime binding model for all VOX capabilities.
A capability is split into:

  VOXCapability
      Stateless global definition shared across workloads.

  VOXBoundCapability
      Per-workload runtime proxy with resolved configuration.

The declarative contract is loaded from ``capability.yml``:

  params:
      Non-secret configuration supplied by the workload manifest and/or
      capability-specific overrides.

  secrets:
      Sensitive configuration managed exclusively by the workload Vault.

Python classes implement behaviour; YAML is the single source of truth for
capability configuration metadata.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from vox.observability import VOXForensicLogger
    from vox.provider import CapabilityHostProtocol

logger = logging.getLogger(__name__)

EXPLAIN_WIDTH = 60
PARAM_COL_WIDTH = 22
DESC_COL_WIDTH = 35


# ------------------------------------------------------------------------------
# YAML contract metadata
# ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParamMeta:
    """Metadata for a non-secret capability parameter."""

    name: str
    description: str
    type: str
    default: Any


@dataclass(frozen=True, slots=True)
class SecretMeta:
    """Metadata for a Vault-managed capability secret."""

    name: str
    description: str
    type: str
    required: bool = True


@dataclass
class CapabilityContract:
    """Declarative contract loaded from capability.yml.

    The contract owns capability identity (``name``, ``version``,
    ``description``, ``provides``), the authoritative ``params``/``secrets``
    namespaces, and the capability's exposed command declarations. Runtime
    implementation is deliberately absent from this metadata.
    """

    name: str
    version: str = ""
    description: str = ""
    provides: list[str] = field(default_factory=list)
    exposed_commands: list[dict] = field(default_factory=list)
    params: dict[str, ParamMeta] = field(default_factory=dict)
    secrets: dict[str, SecretMeta] = field(default_factory=dict)

    @property
    def secret_names(self) -> set[str]:
        return set(self.secrets)

    @property
    def required_secret_names(self) -> set[str]:
        return {name for name, meta in self.secrets.items() if meta.required}

    @property
    def required_params(self) -> set[str]:
        return {
            name
            for name, meta in self.params.items()
            if meta.default is None
        }


def _parse_params(raw_params: dict, source: str) -> dict[str, ParamMeta]:
    """Parse ``params`` section from a raw YAML mapping.

    ``source`` is used in error messages (e.g. file path).
    """
    if not isinstance(raw_params, dict):
        raise TypeError(f"Invalid 'params' section in: {source}")

    params: dict[str, ParamMeta] = {}

    for pname, pdef in raw_params.items():
        if not isinstance(pdef, dict):
            raise TypeError(
                f"Invalid parameter definition '{pname}' in: {source}"
            )

        params[pname] = ParamMeta(
            name=pname,
            description=str(pdef.get("description", "")),
            type=str(pdef.get("type", "string")),
            default=pdef.get("default"),
        )

    return params


def _parse_secrets(raw_secrets: dict, source: str) -> dict[str, SecretMeta]:
    """Parse ``secrets`` section from a raw YAML mapping.

    ``source`` is used in error messages (e.g. file path).
    """
    if not isinstance(raw_secrets, dict):
        raise TypeError(f"Invalid 'secrets' section in: {source}")

    secrets: dict[str, SecretMeta] = {}

    for sname, sdef in raw_secrets.items():
        if not isinstance(sdef, dict):
            raise TypeError(f"Invalid secret definition '{sname}' in: {source}")

        secrets[sname] = SecretMeta(
            name=sname,
            description=str(sdef.get("description", "")),
            type=str(sdef.get("type", "string")),
            required=bool(sdef.get("required", True)),
        )

    return secrets


def _parse_string_list(raw: Any, field_name: str, source: Any) -> list[str]:
    """Parse a top-level YAML list-of-strings metadata field."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError(
            f"Invalid '{field_name}' section in: {source} — expected a list"
        )
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise TypeError(
                f"Invalid '{field_name}' item in {source}: {item!r}"
            )
        values.append(item)
    return values


def _parse_exposed_commands(raw: Any, source: Any) -> list[dict]:
    """Parse the ``exposed_commands`` metadata section.

    Each entry is ``{name, description, method}``. ``method`` defaults to the
    command ``name``.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError(
            f"Invalid 'exposed_commands' section in: {source} — expected a list"
        )
    commands: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise TypeError(
                f"Invalid 'exposed_commands' entry in {source}: {entry!r}"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"exposed command in {source} must declare a string 'name'"
            )
        commands.append(
            {
                "name": name,
                "description": str(entry.get("description", "")),
                "method": str(entry.get("method", name)),
            }
        )
    return commands


def load_capability_yaml(
    capability_file: Path,
) -> CapabilityContract | None:
    """Load the declarative capability contract from capability.yml.

    ``params`` and ``secrets`` are deliberately separate namespaces.
    A parameter cannot simultaneously be a secret.
    """
    yml_path = capability_file.with_name("capability.yml")

    if not yml_path.exists():
        return None

    try:
        raw = yaml.safe_load(yml_path.read_text()) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("Failed to parse %s: %s", yml_path, exc)
        return None

    if not isinstance(raw, dict):
        logger.warning(
            "Invalid capability contract %s: expected a mapping",
            yml_path,
        )
        return None

    raw_params = raw.get("params") or {}
    raw_secrets = raw.get("secrets") or {}

    params = _parse_params(raw_params, str(yml_path))
    secrets = _parse_secrets(raw_secrets, str(yml_path))

    overlap = set(params) & set(secrets)

    if overlap:
        raise ValueError(
            f"Capability contract '{yml_path}' declares the same name "
            f"as both parameter and secret: {sorted(overlap)}"
        )

    return CapabilityContract(
        name=str(raw.get("name", "")),
        version=str(raw.get("version", "")),
        description=str(raw.get("description", "")),
        provides=_parse_string_list(raw.get("provides"), "provides", yml_path),
        exposed_commands=_parse_exposed_commands(
            raw.get("exposed_commands"), yml_path
        ),
        params=params,
        secrets=secrets,
    )


def load_config_yml(
    config_file: Path,
) -> tuple[dict[str, ParamMeta], dict[str, SecretMeta]]:
    """Load ``params`` and ``secrets`` from an arbitrary config.yml.

    Used by capabilities that aggregate sub-component configs (e.g.
    comm.gateway merging adapter config.yml files into its contract).
    Returns ``(params, secrets)`` parsed from the YAML file.
    """
    try:
        raw = yaml.safe_load(config_file.read_text()) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("Failed to parse %s: %s", config_file, exc)
        return {}, {}

    if not isinstance(raw, dict):
        return {}, {}

    raw_params = raw.get("params") or {}
    raw_secrets = raw.get("secrets") or {}

    params = _parse_params(raw_params, str(config_file))
    secrets = _parse_secrets(raw_secrets, str(config_file))

    return params, secrets


# ------------------------------------------------------------------------------
# Capability base class
# ------------------------------------------------------------------------------


class VOXCapability:
    """Base class for all VOX capabilities.

    Capability configuration is defined exclusively by ``capability.yml``.
    There is no legacy Python-side configuration contract.
    """

    CAPABILITY_NAME: str = ""

    # Contract loaded from capability.yml.
    _contract: CapabilityContract | None = None

    id: str
    logger: VOXForensicLogger

    # -- contract loading ------------------------------------------------------

    @classmethod
    def load_contract(
        cls,
        capability_file: Path,
    ) -> CapabilityContract | None:
        """Load and cache the declarative YAML contract.

        The registry should call this once during capability discovery.
        Subsequent lifecycle operations use the cached contract.
        """
        contract = load_capability_yaml(capability_file)

        if contract is None:
            return None

        cls._contract = contract
        return contract

    @classmethod
    def _ensure_contract(cls) -> CapabilityContract:
        """Return the loaded capability contract.

        Contracts are expected to be loaded by the capability registry.
        A capability without a contract is invalid and raises instead of
        silently falling back to a legacy Python declaration.
        """
        contract = cls.__dict__.get("_contract")

        if contract is None:
            raise RuntimeError(
                f"Capability '{cls.CAPABILITY_NAME or cls.__name__}' "
                "has no loaded capability contract"
            )

        return contract

    # -- contract accessors ----------------------------------------------------

    @property
    def name(self) -> str:
        """Public capability identifier from CAPABILITY_NAME, else contract
        name, else the class name (last resort for test doubles)."""
        if self.CAPABILITY_NAME:
            return self.CAPABILITY_NAME

        contract = self._ensure_contract()
        return contract.name or self.__class__.__name__

    @classmethod
    def get_params(cls) -> list[str]:
        return list(cls._ensure_contract().params)

    @classmethod
    def get_secret_names(cls) -> set[str]:
        return cls._ensure_contract().secret_names

    @classmethod
    def get_required_secret_names(cls) -> set[str]:
        """Names of secrets declared ``required: true`` in the contract."""
        return cls._ensure_contract().required_secret_names

    @classmethod
    def get_exposed_commands(cls) -> list[dict]:
        """Declared exposed-command metadata from the contract."""
        return list(cls._ensure_contract().exposed_commands)

    @classmethod
    def get_param_meta(
        cls,
        param_name: str,
    ) -> ParamMeta | None:
        return cls._ensure_contract().params.get(param_name)

    @classmethod
    def get_secret_meta(
        cls,
        secret_name: str,
    ) -> SecretMeta | None:
        return cls._ensure_contract().secrets.get(secret_name)

    # -- informational ---------------------------------------------------------

    @classmethod
    def explain_config(cls) -> str:
        """Return a human-readable description of the capability contract."""
        contract = cls._ensure_contract()

        header = f" Requirements for '{cls.__name__}'"
        lines = [f"\n{header:=^{EXPLAIN_WIDTH}}"]

        if contract.params:
            lines.append("  Parameters:")

            for name, meta in contract.params.items():
                status = (
                    f"[Default: {meta.default}]"
                    if meta.default is not None
                    else "[REQUIRED]"
                )

                lines.append(
                    f"    • {name:<{PARAM_COL_WIDTH}} "
                    f"| {meta.description:<{DESC_COL_WIDTH}} {status}"
                )

        if contract.secrets:
            lines.append("  Secrets (Vault):")

            for name, meta in contract.secrets.items():
                tag = "[REQUIRED]" if meta.required else "[OPTIONAL]"
                lines.append(
                    f"    • {name:<{PARAM_COL_WIDTH}} "
                    f"| {meta.description:<{DESC_COL_WIDTH}} {tag}"
                )

        lines.append("=" * EXPLAIN_WIDTH)

        return "\n".join(lines)

    # -- lifecycle -------------------------------------------------------------

    @classmethod
    async def health_check(cls) -> bool:
        return True

    async def boot(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    # -- mount -----------------------------------------------------------------

    def mount(
        self,
        host: CapabilityHostProtocol,
        overrides: dict[str, Any] | None = None,
    ) -> VOXBoundCapability:
        """Create a per-workload binding from YAML params + manifest overrides.

        Only parameters declared under ``params`` may be overridden.
        Secrets are never accepted through this method.
        """
        contract = self._ensure_contract()
        overrides = overrides or {}

        unknown = set(overrides) - set(contract.params)

        if unknown:
            raise ValueError(
                f"Unknown parameter(s) for {self.name}: {sorted(unknown)}"
            )

        resolved: dict[str, Any] = {
            name: meta.default for name, meta in contract.params.items()
        }

        resolved.update(overrides)

        return VOXBoundCapability(
            self,
            host,
            resolved,
        )


# ------------------------------------------------------------------------------
# Bound capability (per-workload runtime proxy)
# ------------------------------------------------------------------------------


class VOXBoundCapability:
    """Per-workload runtime binding of a capability.

    Holds the resolved (non-secret) parameters and the injected secrets, and
    delegates to a ``CapabilityHostProtocol`` for any services it needs from
    the workload runtime. It never reaches into concrete workload internals.
    """

    def __init__(
        self,
        capability: VOXCapability,
        host: CapabilityHostProtocol,
        params: dict[str, Any],
    ) -> None:
        object.__setattr__(self, "_capability", capability)
        object.__setattr__(self, "_host", host)
        object.__setattr__(self, "_params", params)
        object.__setattr__(self, "_secrets", {})
        object.__setattr__(self, "_instance_attrs", {})
        object.__setattr__(self, "_frozen", False)

    def log(self, message: str) -> None:
        self.logger.info(f"[{self.name}] {message}")

    # -- contract access (public) --------------------------------------------

    def get_secret_names(self) -> set[str]:
        """Names of all secrets declared by the bound capability's contract."""
        return self._capability.get_secret_names()

    def get_required_secret_names(self) -> set[str]:
        """Names of secrets declared ``required: true`` by the contract."""
        return self._capability.get_required_secret_names()

    def get_exposed_commands(self) -> list[dict]:
        """Declared exposed-command metadata of the bound capability."""
        return self._capability.get_exposed_commands()

    def warning(self, message: str) -> None:
        self.logger.warning(f"[{self.name}] {message}")

    def error(self, message: str) -> None:
        self.logger.error(f"[{self.name}] {message}")

    def ok(self, message: str) -> None:
        self.logger.ok(f"[{self.name}] {message}")

    def __setattr__(self, name: str, value: Any) -> None:
        if self._frozen and not name.startswith("_"):
            raise AttributeError("BoundCapability is frozen")

        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self._instance_attrs[name] = value

    def __getattr__(self, name: str) -> Any:
        if name in self._instance_attrs:
            return self._instance_attrs[name]

        if name in self._params:
            return self._params[name]

        if name in self._secrets:
            return self._secrets[name]

        try:
            attr = getattr(self._capability, name)
        except AttributeError:
            raise AttributeError(
                f"Capability [{self._capability.name}] "
                f"has no attribute '{name}'"
            ) from None

        if not callable(attr):
            return attr

        raw = getattr(type(self._capability), name, None)

        if raw is None:
            return attr

        if inspect.iscoroutinefunction(raw):

            async def _wrapper(*args: Any, **kwargs: Any) -> Any:
                return await raw(self, *args, **kwargs)

        else:

            def _wrapper(*args: Any, **kwargs: Any) -> Any:
                return raw(self, *args, **kwargs)

        return _wrapper

    def get_safe_path(
        self,
        sub_dir: str,
        filename: str,
    ) -> Path:
        return self._host.get_safe_path(sub_dir, filename)

    def get_capability(
        self,
        cap_id: str,
    ) -> object | None:
        """Look up another mounted capability through the host registry.

        Capability-to-capability access crosses an explicit host interface
        rather than reaching into a concrete registry implementation.
        """
        return self._host.get_capability(cap_id)

    def validate_params(self) -> list[str]:
        """Return required non-secret parameters that are missing."""
        contract = self._capability._ensure_contract()

        return [
            name
            for name, meta in contract.params.items()
            if meta.default is None and self._params.get(name) is None
        ]

    async def initialize(self) -> None:
        """Validate non-secret parameters and initialize the capability."""
        missing = self.validate_params()

        if missing:
            raise ValueError(
                f"Missing required params for {self.name}: {missing}"
            )

        self.ok("Capability initialized")

    def freeze(self) -> None:
        """Prevent further public attribute mutation."""
        object.__setattr__(self, "_frozen", True)
