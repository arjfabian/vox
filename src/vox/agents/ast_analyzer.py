"""ASTAgentAnalyzer — static AST dependency scanner for role files.

Part of the v0.5.0 State-Isolated Hot-Reload architecture.
Scans role source files at bootstrap to discover required capability
IDs without executing any code.  This keeps filesystem parsing
isolated from the runtime agent lifecycle, which is critical for
safe hot-reload of individual roles.
"""

import ast
from pathlib import Path


class ASTAgentAnalyzer:
    """Utility class for static analysis of role files.

    Uses Python's ``ast`` module to extract capability requirements from
    role source code without importing or executing the module.  Both
    ``REQUIRES`` set declarations and dynamic access patterns (e.g.
    ``self.agent.capabilities["cap_id"].get(...)``) are detected.
    """

    @staticmethod
    def _is_capabilities_chain(node: ast.AST) -> bool:
        """Check whether an AST node represents a capabilities attribute chain."""
        if isinstance(node, ast.Attribute) and node.attr == "capabilities":
            inner = node.value
            return isinstance(inner, ast.Attribute) or isinstance(inner, ast.Name)
        return False

    @staticmethod
    def scan_role_capabilities(role_file: Path) -> set[str]:
        """Return all capability IDs referenced in a role source file.

        Parses the file with ``ast.parse`` and walks the tree looking for:
        * ``REQUIRES = {"cap_id", ...}`` declarations (top-level only)
        * ``self.agent.capabilities["cap_id"]`` subscript access
        * ``self.agent.capabilities.get("cap_id")`` method calls
        """
        cap_ids: set[str] = set()
        try:
            with open(role_file) as f:
                tree = ast.parse(f.read())
        except SyntaxError:
            return cap_ids

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id == "REQUIRES":
                    if isinstance(node.value, (ast.Set, ast.List, ast.Tuple)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                cap_ids.add(elt.value)

        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript):
                slice_val = node.slice
                if isinstance(slice_val, ast.Constant) and isinstance(slice_val.value, str):
                    if ASTAgentAnalyzer._is_capabilities_chain(node.value):
                        cap_ids.add(slice_val.value)
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "get":
                    if ASTAgentAnalyzer._is_capabilities_chain(func.value):
                        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                            cap_ids.add(node.args[0].value)

        return cap_ids
