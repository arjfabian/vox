"""ASTWorkloadAnalyzer — static AST dependency scanner for role files.

Scans role source files at bootstrap to discover referenced capability IDs and
their required secrets without executing any code. This keeps filesystem parsing
isolated from the runtime workload lifecycle, which is critical for safe
hot-reload of individual roles.
"""

import ast
from pathlib import Path


class ASTWorkloadAnalyzer:
    """Utility class for static analysis of role files.

    Uses Python's ``ast`` module to extract capability requirements and required
    secrets from role source code without importing or executing the module.
    Detected declarations:

    * ``REQUIRED_SECRETS = {"cap_id": ["SECRET_1", ...]}`` — secrets that the
      role needs from each capability.
    * ``self.workload.capabilities["cap_id"]`` subscript access.
    * ``self.workload.capabilities.get("cap_id")`` method calls.
    """

    @staticmethod
    def _is_capabilities_chain(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute) and node.attr == "capabilities":
            inner = node.value
            return isinstance(inner, (ast.Attribute, ast.Name))
        return False

    @staticmethod
    def scan_role_capabilities(role_file: Path) -> set[str]:
        """Return all capability IDs referenced in a role source file.

        Parses the file with ``ast.parse`` and walks the tree looking for:
        * ``self.workload.capabilities["cap_id"]`` subscript access
        * ``self.workload.capabilities.get("cap_id")`` method calls
        """
        cap_ids: set[str] = set()
        try:
            with open(role_file) as f:
                tree = ast.parse(f.read())
        except SyntaxError:
            return cap_ids

        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript):
                slice_val = node.slice
                if (
                    isinstance(slice_val, ast.Constant)
                    and isinstance(slice_val.value, str)
                    and ASTWorkloadAnalyzer._is_capabilities_chain(node.value)
                ):
                    cap_ids.add(slice_val.value)
            elif isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "get"
                    and ASTWorkloadAnalyzer._is_capabilities_chain(func.value)
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    cap_ids.add(node.args[0].value)

        return cap_ids

    @staticmethod
    def scan_required_secrets(role_file: Path) -> dict[str, list[str]]:
        """Return ``REQUIRED_SECRETS`` declarations from a role source file.

        Parses the file with ``ast.parse`` and extracts top-level
        ``REQUIRED_SECRETS = {"cap_id": ["SECRET_1", ...]}`` mappings.

        Returns ``{cap_id: [secret_name, ...]}``.
        """
        result: dict[str, list[str]] = {}
        try:
            with open(role_file) as f:
                tree = ast.parse(f.read())
        except (SyntaxError, FileNotFoundError, OSError):
            return result

        for node in ast.iter_child_nodes(tree):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                continue
            target = node.targets[0]
            if not (isinstance(target, ast.Name) and target.id == "REQUIRED_SECRETS"):
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            for key, value in zip(node.value.keys, node.value.values):
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    continue
                cap_id = key.value
                if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                    continue
                secrets = [
                    elt.value
                    for elt in value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
                if secrets:
                    result[cap_id] = secrets

        return result
