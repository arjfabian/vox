#!/usr/bin/env python3
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
import importlib
import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vox.security import AgentVault

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
CAPABILITIES_DIR = Path(__file__).resolve().parent.parent / "src" / "vox" / "capabilities"


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
        if isinstance(obj, type) and issubclass(obj, VOXCapability) and obj is not VOXCapability:
            return obj, None
    return None, "No VOXCapability subclass found"


def _discover_sensitive_params(agent_dir: Path) -> dict[str, dict]:
    """Scan agent roles and return ``{cap_id: {sensitive: [keys], all: {key: desc}}}``."""
    roles_dir = agent_dir / "roles"
    if not roles_dir.exists():
        return {}

    result: dict[str, dict] = {}

    role_files = sorted(
        f for f in roles_dir.glob("*.py")
        if not f.name.startswith("_") and not f.name.endswith("_new.py")
    )
    for rf in role_files:
        role_name = rf.stem
        module_path = f"vox.agents.{agent_dir.name}.roles.{role_name}"
        try:
            mod = importlib.import_module(module_path)
        except ImportError as e:
            print(f"  [!] Skipping role '{role_name}' (import error: {e})")
            continue

        from vox.roles import VOXRole
        role_class = next(
            (obj for obj in mod.__dict__.values()
             if isinstance(obj, type) and issubclass(obj, VOXRole) and obj is not VOXRole),
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
                "sensitive": list(getattr(cls, "SENSITIVE_PARAMS", set())),
                "all": {k: v[0] for k, v in cls.PARAMS.items()},
            }
    return result


def _sync_vault(vault: AgentVault, desired: dict[str, dict]) -> None:
    """Delta-sync vault contents against desired capability-param map."""
    active = vault.list_active()

    # Collect all desired (cap, key) pairs that are sensitive
    desired_active: set[tuple[str, str]] = set()
    desired_info: dict[tuple[str, str], str] = {}

    for cap_id, info in desired.items():
        for key in info["sensitive"]:
            desired_active.add((cap_id, key))
            desired_info[(cap_id, key)] = info["all"].get(key, key)

    # Existing vault entries
    existing_active: set[tuple[str, str]] = set()
    existing_inactive: set[tuple[str, str]] = set()

    for cap_id, keys in active.items():
        for key in keys:
            existing_active.add((cap_id, key))

    try:
        existing_inactive = vault.list_inactive()
    except Exception:
        pass

    # 1. Missing → prompt (loop until non-empty or Ctrl+C)
    missing = desired_active - existing_active - existing_inactive
    for cap_id, key in sorted(missing):
        desc = desired_info.get((cap_id, key), key)
        while True:
            try:
                prompt = f"  {cap_id}.{key} ({desc}): "
                val = getpass(prompt)
            except (KeyboardInterrupt, EOFError):
                print("\n  Aborted by user.")
                sys.exit(0)
            if val:
                break
        vault.set(cap_id, key, val, status="active")
        print(f"    ✓ {cap_id}.{key} saved to vault")

    # 2. Inactive but now required → reactivate
    to_reactivate = desired_active & existing_inactive
    for cap_id, key in sorted(to_reactivate):
        vault.activate(cap_id, key)
        print(f"  → {cap_id}.{key} re-activated")

    # 3. Active but no longer desired → deactivate
    orphaned = existing_active - desired_active
    for cap_id, key in sorted(orphaned):
        vault.disable(cap_id, key)
        print(f"  → {cap_id}.{key} orphaned — marked inactive")


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision secrets into agent vault")
    parser.add_argument("--agent", required=True, help="Agent folder name (e.g. tina, leah)")
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

    print(f"Scanning roles for '{args.agent}'...")
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
    _sync_vault(vault, desired)

    print("\nDone. Restart VOX for changes to take effect.")


if __name__ == "__main__":
    main()
