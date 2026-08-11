"""
tools/provision_vault.py

Provision secrets into an agent's encrypted ``secrets.vault``.

Usage::

    export VOX_MASTER_KEY="your-strong-passphrase"
    python tools/provision_vault.py --agent tina

Scans the agent's roles, discovers required capabilities and their
sensitive params, then performs a delta sync against the vault:

* **Missing** — prompts securely via ``getpass``, encrypts, stores *active*.
* **Orphaned** — vault rows for param no longer required → marked *inactive*.
* **Re-activated** — inactive row now required → switched back to *active*.
"""

import argparse
import asyncio
import importlib
import sys
from getpass import getpass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT_DIR / "agents"
CAPABILITIES_DIR = ROOT_DIR / "src" / "vox" / "capabilities"
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(1, str(ROOT_DIR))

from vox.security import AgentVault

FLEET_MANDATORY: list[tuple[str, str]] = [
    ("comm.gateway", "TELEGRAM_BOT_TOKEN"),
]


def _load_capability_class(cap_id: str):
    """Import and return a capability class by ID (e.g. ``comm.email``)."""
    from vox.capabilities.base import VOXCapability

    parts = cap_id.split(".")
    module_path = f"vox.capabilities.{'.'.join(parts)}.capability"
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        return None, str(e)
    for obj in module.__dict__.values():
        if (
            isinstance(obj, type)
            and issubclass(obj, VOXCapability)
            and obj is not VOXCapability
        ):
            return obj, None
    return None, "No VOXCapability subclass found"


def _discover_sensitive_params(agent_dir: Path) -> dict[str, dict]:
    """Scan agent roles and return ``{cap_id: {sensitive: [keys], all: {key: desc}}}``.

    ``sensitive`` contains ``(key, required)`` tuples where *required* is
    ``True`` when the PARAMS default is ``None`` (no implicit value).
    """
    result: dict[str, dict] = {}

    def _categorise_sensitive(cls) -> list[tuple[str, bool]]:
        """Return ``(key, required)`` pairs for sensitive params."""
        return [
            (key, cls.PARAMS.get(key, [None, None])[1] is None)
            for key in getattr(cls, "SENSITIVE_PARAMS", set())
        ]

    # Fleet-mandatory capabilities — always provisioned regardless of roles
    for cap_id, _ in FLEET_MANDATORY:
        if cap_id not in result:
            cls, err = _load_capability_class(cap_id)
            if cls is not None:
                result[cap_id] = {
                    "sensitive": _categorise_sensitive(cls),
                    "all": {k: v[0] for k, v in cls.PARAMS.items()},
                }

    # Role-based discovery
    roles_dir = agent_dir / "roles"
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
            cls, err = _load_capability_class(cap_id)
            if cls is None:
                print(f"  [!] Skipping capability '{cap_id}' (load error: {err})")
                continue
            result[cap_id] = {
                "sensitive": _categorise_sensitive(cls),
                "all": {k: v[0] for k, v in cls.PARAMS.items()},
            }
    return result


async def _sync_vault(vault: AgentVault, desired: dict[str, dict]) -> None:
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
    parser = argparse.ArgumentParser(description="Provision secrets into agent vault")
    parser.add_argument(
        "--agent", required=True, help="Agent folder name (e.g. tina, leah)"
    )
    args = parser.parse_args()

    agent_dir = AGENTS_DIR / args.agent.lower()
    if not agent_dir.exists():
        print(f"Error: agent directory not found: {agent_dir}")
        sys.exit(1)

    # Read manifest for agent UUID
    import yaml

    manifest_path = agent_dir / "agent.yml"
    if not manifest_path.exists():
        print(f"Error: no agent.yml in {agent_dir}")
        sys.exit(1)
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    agent_id = manifest.get("id", "")
    if not agent_id:
        print(f"Error: agent '{args.agent}' has no 'id' in manifest")
        sys.exit(2)

    print(
        f"Scanning agent '{args.agent}' (fleet-mandatory: {[c for c, _ in FLEET_MANDATORY]})..."
    )
    desired = _discover_sensitive_params(agent_dir)

    if not desired:
        print("No sensitive-capability requirements found.")
        return

    print("\nDiscovered capability requirements:")
    for cap_id, info in desired.items():
        print(f"  {cap_id}: {', '.join(info['sensitive']) or '(none sensitive)'}")

    print(f"\nOpening vault for '{args.agent}'...")
    try:
        vault = AgentVault(agent_dir, agent_id)
    except RuntimeError as e:
        print(f"Error: {e}")
        sys.exit(3)

    print("\nSynchronizing vault...")
    await _sync_vault(vault, desired)

    print("\nDone. Restart VOX for changes to take effect.")


if __name__ == "__main__":
    asyncio.run(main())
