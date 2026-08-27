"""
tools/provision_vault.py

Provision secrets into a workload's encrypted ``secrets.vault``.

Usage::

    export VOX_MASTER_KEY="your-strong-passphrase"
    python tools/provision_vault.py --workload <workload_name>

Scans the workload's roles, discovers required capabilities and their
secrets from YAML contracts, then performs a delta sync against the vault:

* **Missing** — prompts securely via ``getpass``, encrypts, stores *active*.
* **Orphaned** — vault rows for secret no longer required → marked *inactive*.
* **Re-activated** — inactive row now required → switched back to *active*.
"""

import argparse
import asyncio
import importlib
import sys
from getpass import getpass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PERSONAS_DIR = ROOT_DIR / "instance" / "personas"
CAPABILITIES_DIR = ROOT_DIR / "src" / "vox" / "capabilities"
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(1, str(ROOT_DIR))

from vox.security import WorkloadVault

FLEET_MANDATORY: list[tuple[str, str]] = [
    ("comm.gateway", "TELEGRAM_BOT_TOKEN"),
]


def _load_capability_contract(cap_id: str):
    """Load a capability's YAML contract by ID (e.g. ``comm.email``).

    Returns ``(CapabilityContract, None)`` on success or
    ``(None, error_message)`` on failure.
    """
    from vox.capabilities.base import VOXCapability, load_capability_yaml

    parts = cap_id.split(".")
    module_path = f"vox.capabilities.{'.'.join(parts)}.capability"
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        return None, str(e)
    capability_class = next(
        (
            obj
            for obj in module.__dict__.values()
            if isinstance(obj, type)
            and issubclass(obj, VOXCapability)
            and obj is not VOXCapability
        ),
        None,
    )
    if capability_class is None:
        return None, "No VOXCapability subclass found"

    contract = capability_class._contract
    if contract is None:
        capability_file = Path(module.__file__).resolve()
        contract = load_capability_yaml(capability_file)
    if contract is None:
        return None, f"No capability.yml found for {cap_id}"
    return contract, None


def _load_gateway_contract_secrets() -> dict[str, dict]:
    """Load comm.gateway contract secrets by scanning adapter config.yml files.

    The gateway aggregates secrets from per-adapter config.yml files.
    Returns ``{cap_id: {"sensitive": [...], "all": {...}}}`` for the gateway.
    """
    from vox.capabilities.base import load_config_yml

    gateway_dir = CAPABILITIES_DIR / "comm" / "gateway"
    adapter_dir = gateway_dir / "adapters"

    if not adapter_dir.is_dir():
        return {}

    secrets: dict[str, tuple[str, bool, str]] = {}

    for adapter_entry in sorted(adapter_dir.iterdir()):
        config_yml = adapter_entry / "config.yml"
        if not config_yml.is_file():
            continue
        _params, adapter_secrets = load_config_yml(config_yml)
        for name, meta in adapter_secrets.items():
            secrets[name] = (name, meta.required, meta.description)

    if not secrets:
        return {}

    sensitive = [(name, required) for name, required, _desc in secrets.values()]
    all_descs = {name: desc for name, _, desc in secrets.values()}

    return {
        "comm.gateway": {
            "sensitive": sensitive,
            "all": all_descs,
        },
    }


def _discover_sensitive_params(persona_dir: Path) -> dict[str, dict]:
    """Scan workload roles and return ``{cap_id: {sensitive: [keys], all: {key: desc}}}``.

    Uses the YAML capability contract as the single source of truth.
    ``sensitive`` contains ``(key, required)`` tuples where *required* is
    ``True`` when the secret is marked ``required: true`` in the YAML.
    """
    result: dict[str, dict] = {}

    def _categorise_secrets(contract) -> list[tuple[str, bool]]:
        """Return ``(key, required)`` pairs for Vault-managed secrets."""
        return [
            (name, meta.required)
            for name, meta in contract.secrets.items()
        ]

    def _all_descriptions(contract) -> dict[str, str]:
        """Return ``{secret: description}`` from contract secrets."""
        return {name: meta.description for name, meta in contract.secrets.items()}

    # Fleet-mandatory capabilities — always provisioned regardless of roles.
    # The gateway has aggregated secrets from adapter config.yml files.
    gateway_secrets = _load_gateway_contract_secrets()
    for cap_id, info in gateway_secrets.items():
        result[cap_id] = info

    # Also load any non-gateway fleet-mandatory capabilities
    for cap_id, _ in FLEET_MANDATORY:
        if cap_id in result:
            continue
        contract, err = _load_capability_contract(cap_id)
        if contract is not None:
            result[cap_id] = {
                "sensitive": _categorise_secrets(contract),
                "all": _all_descriptions(contract),
            }

    # Role-based discovery
    roles_dir = persona_dir / "roles"
    if not roles_dir.exists():
        return result

    role_files = sorted(
        f
        for f in roles_dir.glob("*.py")
        if not f.name.startswith("_") and not f.name.endswith("_new.py")
    )
    for rf in role_files:
        role_name = rf.stem

        import importlib.util

        try:
            spec = importlib.util.spec_from_file_location(role_name, rf)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot create spec for {rf}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:  # noqa: BLE001 — import failure skips role
            print(f"  [!] Skipping role '{role_name}' (import error: {e})")
            continue

        from vox.roles import VOXRole

        role_class = next(
            (
                obj
                for obj in mod.__dict__.values()
                if isinstance(obj, type)
                and issubclass(obj, VOXRole)
                and obj is not VOXRole
            ),
            None,
        )
        if role_class is None:
            continue

        for cap_id in getattr(role_class, "REQUIRES", set()):
            if cap_id in result:
                continue
            contract, err = _load_capability_contract(cap_id)
            if contract is None:
                print(f"  [!] Skipping capability '{cap_id}' (load error: {err})")
                continue
            result[cap_id] = {
                "sensitive": _categorise_secrets(contract),
                "all": _all_descriptions(contract),
            }
    return result


async def _sync_vault(vault: WorkloadVault, desired: dict[str, dict]) -> None:
    """Delta-sync vault contents against desired capability-param map."""
    active = await vault.list_active()

    # Collect all desired (cap, key) pairs that are sensitive
    desired_active: set[tuple[str, str]] = set()
    desired_info: dict[tuple[str, str], str] = {}
    optional_keys: set[tuple[str, str]] = set()

    for cap_id, info in desired.items():
        for key, required in info["sensitive"]:
            desired_active.add((cap_id, key))
            desired_info[(cap_id, key)] = info["all"].get(key, key)
            if not required:
                optional_keys.add((cap_id, key))

    # Existing vault entries
    existing_active: set[tuple[str, str]] = set()
    existing_inactive: set[tuple[str, str]] = set()

    for cap_id, keys in active.items():
        for key in keys:
            existing_active.add((cap_id, key))

    try:
        existing_inactive = await vault.list_inactive()
    except Exception:  # noqa: BLE001, S110 — list_inactive may not exist in old vaults
        pass

    # 1. Missing → prompt (loop until non-empty or Ctrl+C)
    missing = desired_active - existing_active - existing_inactive
    for cap_id, key in sorted(missing):
        desc = desired_info.get((cap_id, key), key)
        is_optional = (cap_id, key) in optional_keys
        while True:
            try:
                tag = " [OPTIONAL]" if is_optional else ""
                prompt = f"  {cap_id}.{key}{tag} ({desc}): "
                val = getpass(prompt)
            except (KeyboardInterrupt, EOFError):
                print("\n  Aborted by user.")
                sys.exit(0)
            if val:
                break
            if is_optional:
                print("    — skipped (optional)")
                break
        if val:
            await vault.set(cap_id, key, val, status="active")
            print(f"    ✓ {cap_id}.{key} saved to vault")

    # 2. Inactive but now required → reactivate
    to_reactivate = desired_active & existing_inactive
    for cap_id, key in sorted(to_reactivate):
        await vault.activate(cap_id, key)
        print(f"  → {cap_id}.{key} re-activated")

    # 3. Active but no longer desired → deactivate
    orphaned = existing_active - desired_active
    for cap_id, key in sorted(orphaned):
        await vault.disable(cap_id, key)
        print(f"  → {cap_id}.{key} orphaned — marked inactive")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision secrets into workload vault"
    )
    parser.add_argument("--workload", required=True, help="Workload folder name")
    args = parser.parse_args()

    persona_dir = PERSONAS_DIR / args.workload.lower()
    if not persona_dir.exists():
        print(f"Error: persona directory not found: {persona_dir}")
        sys.exit(1)

    # Read manifest for workload UUID
    import yaml

    manifest_path = persona_dir / "manifest.yml"
    if not manifest_path.exists():
        print(f"Error: no manifest.yml in {persona_dir}")
        sys.exit(1)
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    workload_id = manifest.get("id", "")
    if not workload_id:
        print(f"Error: workload '{args.workload}' has no 'id' in manifest")
        sys.exit(2)

    print(
        f"Scanning workload '{args.workload}' (fleet-mandatory: {[c for c, _ in FLEET_MANDATORY]})..."
    )
    desired = _discover_sensitive_params(persona_dir)

    if not desired:
        print("No sensitive-capability requirements found.")
        return

    print("\nDiscovered capability secrets:")
    for cap_id, info in desired.items():
        print(
            f"  {cap_id}: "
            f"{', '.join(key for key, _ in info['sensitive']) or '(none)'}"
        )

    print(f"\nOpening vault for '{args.workload}'...")
    try:
        vault = WorkloadVault(persona_dir, workload_id)
    except RuntimeError as e:
        print(f"Error: {e}")
        sys.exit(3)

    print("\nSynchronizing vault...")
    await _sync_vault(vault, desired)

    print("\nDone. Restart VOX for changes to take effect.")


if __name__ == "__main__":
    asyncio.run(main())
