"""
tools/provision_vault.py

Provision secrets into a workload's encrypted ``secrets.vault``.

Usage::

    export VOX_MASTER_KEY="your-strong-passphrase"
    python tools/provision_vault.py --workload <workload_name>

Scans the workload's roles (AST, no execution), discovers required
capabilities and the secrets those roles actually require, then performs a
delta sync against the vault:

* **Missing** — prompts securely via ``getpass``, encrypts, stores *active*.
* **Orphaned** — vault rows for secret no longer required → marked *inactive*.
* **Re-activated** — inactive row now required → switched back to *active*.

Workload scoping mirrors ``CapabilityBinder``: a secret is provisioned only
when the aggregated capability contract marks it ``required: true`` AND at
least one loaded role declares it in ``REQUIRED_SECRETS``. A workload never
requires secrets merely because an adapter exists elsewhere in the repository.

Note on scope: role ``REQUIRED_SECRETS`` is used here as the *current
sanctioned provisioning signal* — it tells this tool what a workload must have
in its vault. It is intentionally NOT treated as the architectural definition
of adapter availability: availability remains a distinct runtime/composition
concern (adapter ``is_configured`` and manifest scoping) that this tool must
not become the authority over.
"""

import argparse
import asyncio
import importlib
import sys
from getpass import getpass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PERSONAS_DIR = ROOT_DIR / "instance" / "personas"
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(1, str(ROOT_DIR))

from vox.security import WorkloadVault


def _load_capability_contract(cap_id: str):
    """Load a capability's aggregated YAML contract by ID (e.g. ``comm.email``).

    Uses the capability's own ``load_contract()`` so adapter-based ports (the
    ``comm.gateway`` / ``ai.llm`` pattern) contribute their merged
    per-adapter config — the same contract the runtime registry builds.

    Returns ``(CapabilityContract, None)`` on success or
    ``(None, error_message)`` on failure.
    """
    from vox.capabilities.base import VOXCapability

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

    capability_file = Path(module.__file__).resolve()
    try:
        contract = capability_class.load_contract(capability_file)
    except (ValueError, TypeError, RuntimeError) as e:
        return None, str(e)
    if contract is None:
        return None, f"No capability.yml found for {cap_id}"
    return contract, None


def _discover_sensitive_params(persona_dir: Path) -> dict[str, dict]:
    """Scan workload roles and discover required vault-managed secrets.

    Mirrors ``CapabilityBinder._collect_required_secrets()``: the workload's
    required capabilities come from role source (AST ``scan_role_capabilities``,
    replicating ``discover_and_mount``), and a secret is *provisioned* only
    when the aggregated capability contract marks it ``required: true`` AND at
    least one loaded role declares it in ``REQUIRED_SECRETS`` (AST
    ``scan_required_secrets``). Optional secrets are not prompted.

    ``REQUIRED_SECRETS`` is the current *provisioning signal* for this tool.
    It must not be read as the architectural definition of adapter availability
    (see module docstring).

    Returns ``{cap_id: {"sensitive": [(key, required)], "all": ...}}`` where
    ``sensitive`` contains only secrets the workload actually requires.
    """
    import importlib.util

    from vox.workloads.ast_analyzer import ASTWorkloadAnalyzer

    result: dict[str, dict] = {}

    roles_dir = persona_dir / "roles"
    if not roles_dir.exists():
        return result

    role_files = sorted(
        f
        for f in roles_dir.glob("*.py")
        if not f.name.startswith("_") and not f.name.endswith("_new.py")
    )

    required_caps: set[str] = set()
    role_required_secrets: dict[str, set[str]] = {}

    for rf in role_files:
        role_name = rf.stem

        # Import probe for "loaded role" parity with the runtime: a role that
        # fails to import contributes no requirements. Requirements themselves
        # are read statically via AST, never from executing role code.
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

        required_caps |= ASTWorkloadAnalyzer.scan_role_capabilities(rf)
        for cap_id, names in ASTWorkloadAnalyzer.scan_required_secrets(rf).items():
            role_required_secrets.setdefault(cap_id, set()).update(names)

    for cap_id in sorted(required_caps):
        contract, err = _load_capability_contract(cap_id)
        if contract is None:
            print(
                f"  [!] Skipping capability '{cap_id}' "
                f"(load error: {err})"
            )
            continue

        declared = role_required_secrets.get(cap_id, set())
        selected = contract.required_secret_names & declared
        if not selected:
            continue

        result[cap_id] = {
            "sensitive": [(name, True) for name in sorted(selected)],
            "all": {
                name: contract.secrets[name].description
                for name in sorted(selected)
            },
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
    except Exception:  # noqa: BLE001, S110 — old vault compat
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
    parser.add_argument(
        "--workload",
        required=True,
        help="Workload folder name",
    )
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

    print(f"Scanning workload '{args.workload}' (role-scanned, AST)...")
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
