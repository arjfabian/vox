#!/usr/bin/env python3
"""Statically inspect a VOX agent directory for manifest, roles, and capability dependencies."""

import ast
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

def _ok(msg):
    return f"\033[32m[OK]\033[0m {msg}"

def _warn(msg):
    return f"\033[33m[WARN]\033[0m {msg}"

def _err(msg):
    return f"\033[31m[ERROR]\033[0m {msg}"

def _crit(msg):
    return f"\033[41m\033[37m[CRITICAL]\033[0m {msg}"

def _info(msg):
    return f"\033[36m[INFO]\033[0m {msg}"

def _heading(msg):
    return f"\n\033[1;34m=== {msg} ===\033[0m"


# ---------------------------------------------------------------------------
# 1. Manifest parsing
# ---------------------------------------------------------------------------

def load_manifest(agent_dir: Path) -> dict:
    manifest_path = agent_dir / "agent.yml"
    if not manifest_path.exists():
        return {"error": f"agent.yml not found in {agent_dir}"}
    try:
        import yaml
        with open(manifest_path, "r") as f:
            data = yaml.safe_load(f) or {}
        return {
            "name": data.get("name"),
            "id": data.get("id"),
            "master_id": data.get("master_id", ""),
            "autostart": data.get("autostart", False),
            "roles_allowlist": data.get("roles", []),
            "personality": data.get("personality", {}),
        }
    except Exception as e:
        return {"error": f"Failed to parse agent.yml: {e}"}


# ---------------------------------------------------------------------------
# 2. Role discovery (AST-based, no imports)
# ---------------------------------------------------------------------------

def find_role_files(agent_dir: Path) -> list[Path]:
    roles_dir = agent_dir / "roles"
    if not roles_dir.exists():
        return []
    top_level = sorted(
        p for p in roles_dir.glob("*.py")
        if not p.name.startswith("_") and not p.name.endswith("_new.py")
    )
    sub = sorted(
        p for p in roles_dir.rglob("*.py")
        if "__pycache__" not in p.parts
        and p.parent != roles_dir
    )
    return top_level, sub


def extract_requires_from_ast(tree: ast.AST, source: str) -> set[str]:
    requires: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.target.id == "REQUIRES":
                vals = _extract_set_literals(item.value)
                requires.update(vals)
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == "REQUIRES":
                        vals = _extract_set_literals(item.value)
                        requires.update(vals)
    return requires


def _extract_set_literals(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Set):
        return [
            elt.value for elt in node.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        ]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "set":
        if node.args:
            arg = node.args[0]
            if isinstance(arg, ast.List):
                return [
                    elt.value for elt in arg.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
        return []
    return []


def _is_capabilities_chain(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "capabilities":
        inner = node.value
        return isinstance(inner, ast.Attribute) or isinstance(inner, ast.Name)
    return False


def extract_cap_usage_from_ast(tree: ast.AST) -> set[str]:
    caps: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            _walk_subscript_cap(node, caps)
        elif isinstance(node, ast.Call):
            _walk_get_call_cap(node, caps)
    return caps


def _walk_subscript_cap(node: ast.Subscript, acc: set[str]) -> None:
    if not _is_capabilities_chain(node.value):
        return
    if not isinstance(node.slice, ast.Constant) or not isinstance(node.slice.value, str):
        return
    acc.add(node.slice.value)


def _walk_get_call_cap(node: ast.Call, acc: set[str]) -> None:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "get":
        return
    if not _is_capabilities_chain(func.value):
        return
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        acc.add(node.args[0].value)


def extract_command_decorators(tree: ast.AST) -> list[dict]:
    commands = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in item.decorator_list:
                if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Name) and deco.func.id == "command":
                    meta = {"name": None, "description": "", "requires": set()}
                    for kw in deco.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                            meta["name"] = kw.value.value
                        elif kw.arg == "description" and isinstance(kw.value, ast.Constant):
                            meta["description"] = kw.value.value
                        elif kw.arg == "requires" and isinstance(kw.value, (ast.List, ast.Set, ast.Tuple)):
                            for elt in kw.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    meta["requires"].add(elt.value)
                    commands.append(meta)
    return commands


# ---------------------------------------------------------------------------
# 3. Delta analysis
# ---------------------------------------------------------------------------

def compute_delta(
    static_requires: set[str],
    cap_usage: set[str],
) -> dict:
    used_but_not_required = sorted(cap_usage - static_requires)
    required_but_not_used = sorted(static_requires - cap_usage)
    return {
        "required_but_not_used": required_but_not_used,
        "used_but_not_required": used_but_not_required,
        "static_requires": sorted(static_requires),
        "cap_usage": sorted(cap_usage),
    }


# ---------------------------------------------------------------------------
# 4. Report
# ---------------------------------------------------------------------------

def print_manifest_report(manifest: dict) -> None:
    print(_heading("Manifest"))
    if "error" in manifest:
        print(_crit(manifest["error"]))
        return
    print(_ok(f"Name:           {manifest.get('name', '?')}"))
    print(_ok(f"ID:             {manifest.get('id', '?')}"))
    print(_ok(f"Master ID:      {manifest.get('master_id', '') or '(root)'}"))
    print(_ok(f"Autostart:      {manifest.get('autostart', False)}"))
    print(_info("Capabilities: inferred from role REQUIRES + runtime usage"))


def _fmt_cap(c: str) -> str:
    return c


def print_role_report(
    role_file: Path,
    requires: set[str],
    cap_usage: set[str],
    commands: list[dict],
) -> None:
    rel = role_file.relative_to(role_file.anchor) if role_file.is_absolute() else role_file
    print(f"\n  Role: {role_file.stem}  ({rel})")
    if requires:
        for r in sorted(requires):
            print(f"    {_ok(f'REQUIRES: {r}')}")
    else:
        print(f"    {_info('No REQUIRES declaration')}")
    if cap_usage:
        for c in sorted(cap_usage):
            print(f"    {_info(f'Uses: {_fmt_cap(c)}')}")
    for cmd in commands:
        r = cmd.get("name") or "(unnamed)"
        desc = cmd.get("description", "")
        if desc:
            print(f"    {_info(f'Command: {r} — {desc}')}")
        else:
            print(f"    {_info(f'Command: {r}')}")


def print_delta_report(delta: dict) -> None:
    print(_heading("Capability Coverage"))
    if delta["used_but_not_required"]:
        for cap in delta["used_but_not_required"]:
            print(_warn(f"Runtime usage of '{cap}' but no REQUIRES declaration"))
    if delta["required_but_not_used"]:
        for cap in delta["required_but_not_used"]:
            print(_warn(f"REQUIRES '{cap}' but no runtime usage found"))
    if not delta["used_but_not_required"] and not delta["required_but_not_used"]:
        print(_ok("All capability references are consistent (REQUIRES ↔ runtime usage)"))


# ---------------------------------------------------------------------------
# 5. Main
# ---------------------------------------------------------------------------

def inspect_agent(agent_name: str) -> int:
    base = Path(__file__).resolve().parent.parent / "agents"
    candidate = Path(agent_name)
    if candidate.is_dir():
        agent_dir = candidate.resolve()
    else:
        agent_dir = (base / agent_name).resolve()
        if not agent_dir.exists():
            print(_err(f"Agent directory not found: {agent_dir}"))
            return 1

    agent_dir = agent_dir.resolve()
    print(_heading(f"Inspecting Agent: {agent_dir.name}"))
    print(f"  Path: {agent_dir}")

    errors = 0

    manifest = load_manifest(agent_dir)
    print_manifest_report(manifest)

    top_level, sub = find_role_files(agent_dir)
    if not top_level and not sub:
        print(_heading("Roles"))
        print(_warn("No role files found in roles/"))
    else:
        print(_heading("Roles"))
        total_static_requires: set[str] = set()
        total_cap_usage: set[str] = set()

        if sub:
            print(f"  ({len(sub)} submodule(s) found but not loaded directly —"
                  f" VOX only loads top-level role files)\n")

        for rf in top_level:
            try:
                source = rf.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(rf))
                requires = extract_requires_from_ast(tree, source)
                cap_usage = extract_cap_usage_from_ast(tree)
                commands = extract_command_decorators(tree)
                total_static_requires.update(requires)
                total_cap_usage.update(cap_usage)
                print_role_report(rf, requires, cap_usage, commands)
            except SyntaxError as e:
                print(f"\n  Role: {rf.stem}")
                print(f"    {_err(f'Syntax error in {rf.name}: {e}')}")
                errors += 1

    delta = compute_delta(total_static_requires, total_cap_usage)
    print_delta_report(delta)

    print()
    if errors:
        print(_err(f"Inspection complete — {errors} error(s) found."))
    else:
        print(_ok("Inspection complete — no errors."))
    return 0 if errors == 0 else 1


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <agent_name_or_path>")
        print(f"Example: {sys.argv[0]} tina")
        return 1
    return inspect_agent(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
