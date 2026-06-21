"""Static audit to prevent agent registry and catalog drift."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


class RegistrySyncViolation:
    """Represents a registry sync violation."""

    def __init__(self, file_path: str, line: int, category: str, detail: str) -> None:
        self.file_path = file_path
        self.line = line
        self.category = category
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [AGENT-REGISTRY-SYNC] {self.category}: {self.detail}"


def _find_builtin_supports_tuple(tree: ast.Module) -> ast.expr | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_BUILTIN_AGENT_SUPPORTS":
                    return node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_BUILTIN_AGENT_SUPPORTS"
        ):
            return node.value
    return None


def _parse_support_entry(elt: ast.expr) -> tuple[str | None, str | None, bool]:
    is_correct_call = (
        isinstance(elt, ast.Call)
        and isinstance(elt.func, ast.Attribute)
        and (
            (
                elt.func.attr == "from_registration_kwargs"
                and isinstance(elt.func.value, ast.Name)
                and elt.func.value.id == "AgentSupport"
            )
            or (
                elt.func.attr == "to_support"
                and isinstance(elt.func.value, ast.Call)
                and isinstance(elt.func.value.func, ast.Name)
                and elt.func.value.func.id == "BuiltinAgentSpec"
            )
        )
    )
    if not is_correct_call:
        return None, None, False

    assert isinstance(elt, ast.Call)
    name_val: str | None = None
    cmd_val: str | None = None

    if elt.args:
        first_arg = elt.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            name_val = first_arg.value
    for kw in elt.keywords:
        if (
            kw.arg == "name"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            name_val = kw.value.value
        elif (
            kw.arg == "cmd"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            cmd_val = kw.value.value

    return name_val, cmd_val, True


def audit_builtin_file(content: str, rel_path: str) -> list[RegistrySyncViolation]:
    violations: list[RegistrySyncViolation] = []
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        violations.append(RegistrySyncViolation(rel_path, e.lineno or 1, "syntax_error", str(e)))
        return violations

    builtin_supports_tuple = _find_builtin_supports_tuple(tree)

    if builtin_supports_tuple is None:
        violations.append(
            RegistrySyncViolation(
                rel_path, 1, "missing_constant", "_BUILTIN_AGENT_SUPPORTS is not defined"
            )
        )
        return violations

    if not isinstance(builtin_supports_tuple, ast.Tuple):
        violations.append(
            RegistrySyncViolation(
                rel_path,
                builtin_supports_tuple.lineno,
                "invalid_constant",
                "_BUILTIN_AGENT_SUPPORTS must be a tuple",
            )
        )
        return violations

    names: list[str] = []
    cmds: list[str] = []

    for elt in builtin_supports_tuple.elts:
        name_val, cmd_val, is_correct = _parse_support_entry(elt)
        if not is_correct:
            violations.append(
                RegistrySyncViolation(
                    rel_path,
                    elt.lineno,
                    "invalid_support_entry",
                    "Entry must be constructed using AgentSupport.from_registration_kwargs "
                    "or BuiltinAgentSpec(...).to_support(name)",
                )
            )
            continue

        if name_val is not None:
            names.append(str(name_val))
            if cmd_val is None:
                cmd_val = name_val
        if cmd_val is not None:
            cmds.append(str(cmd_val))

    if len(names) != len(set(names)):
        violations.append(
            RegistrySyncViolation(
                rel_path,
                builtin_supports_tuple.lineno,
                "duplicate_names",
                f"Built-in agent names must be unique (got: {names})",
            )
        )

    if len(cmds) != len(set(cmds)):
        violations.append(
            RegistrySyncViolation(
                rel_path,
                builtin_supports_tuple.lineno,
                "duplicate_cmds",
                f"Built-in agent cmds must be unique (got: {cmds})",
            )
        )

    return violations


def _check_registry_seed_calls(
    registry_class: ast.ClassDef, rel_path: str
) -> list[RegistrySyncViolation]:
    violations = []
    has_init_seed = False
    has_from_config_seed = False
    has_unregister = False

    for node in registry_class.body:
        if isinstance(node, ast.FunctionDef):
            if node.name == "__init__":
                for subnode in ast.walk(node):
                    if (
                        isinstance(subnode, ast.Call)
                        and isinstance(subnode.func, ast.Name)
                        and subnode.func.id == "_seed_catalog_with_builtins"
                    ):
                        has_init_seed = True
            elif node.name == "from_config":
                for subnode in ast.walk(node):
                    if (
                        isinstance(subnode, ast.Call)
                        and isinstance(subnode.func, ast.Name)
                        and subnode.func.id == "_seed_catalog_with_builtins"
                    ):
                        has_from_config_seed = True
            elif node.name == "unregister":
                has_unregister = True

    if not has_init_seed:
        violations.append(
            RegistrySyncViolation(
                rel_path,
                registry_class.lineno,
                "missing_seed_call",
                "_seed_catalog_with_builtins is not called in AgentRegistry.__init__",
            )
        )
    if not has_from_config_seed:
        violations.append(
            RegistrySyncViolation(
                rel_path,
                registry_class.lineno,
                "missing_seed_call",
                "_seed_catalog_with_builtins is not called in AgentRegistry.from_config",
            )
        )
    if not has_unregister:
        violations.append(
            RegistrySyncViolation(
                rel_path,
                registry_class.lineno,
                "missing_unregister",
                "AgentRegistry.unregister method is not defined",
            )
        )
    return violations


def audit_registry_file(content: str, rel_path: str) -> list[RegistrySyncViolation]:
    violations: list[RegistrySyncViolation] = []
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        violations.append(RegistrySyncViolation(rel_path, e.lineno or 1, "syntax_error", str(e)))
        return violations

    registry_class = None
    builtin_agents_func = None

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AgentRegistry":
            registry_class = node
        elif isinstance(node, ast.FunctionDef) and node.name == "builtin_agents":
            builtin_agents_func = node

    if registry_class is None:
        violations.append(
            RegistrySyncViolation(
                rel_path, 1, "missing_class", "AgentRegistry class is not defined"
            )
        )
    else:
        violations.extend(_check_registry_seed_calls(registry_class, rel_path))

    if builtin_agents_func is None:
        violations.append(
            RegistrySyncViolation(
                rel_path, 1, "missing_builtin_agents", "builtin_agents function is not defined"
            )
        )
    else:
        uses_builtin_supports = False
        for subnode in ast.walk(builtin_agents_func):
            if (
                isinstance(subnode, ast.Call)
                and isinstance(subnode.func, ast.Name)
                and subnode.func.id == "builtin_supports"
            ):
                uses_builtin_supports = True

        if not uses_builtin_supports:
            violations.append(
                RegistrySyncViolation(
                    rel_path,
                    builtin_agents_func.lineno,
                    "non_derived_view",
                    "builtin_agents() must return a derived view from builtin_supports()",
                )
            )

    return violations


def _find_requires_pty_if_node(invoke_agent_func: ast.FunctionDef) -> ast.If | None:
    for node in ast.walk(invoke_agent_func):
        if isinstance(node, ast.If) and "requires_pty" in ast.dump(node.test):
            has_sub_call = False
            has_nested_if = False
            for stmt in node.orelse:
                for subnode in ast.walk(stmt):
                    if isinstance(subnode, ast.If):
                        has_nested_if = True
                    if (
                        isinstance(subnode, ast.Call)
                        and isinstance(subnode.func, ast.Name)
                        and subnode.func.id == "run_subprocess_and_read_lines"
                    ):
                        has_sub_call = True

            if has_sub_call and not has_nested_if:
                return node
    return None


def audit_invoke_file(content: str, rel_path: str) -> list[RegistrySyncViolation]:
    violations: list[RegistrySyncViolation] = []
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        violations.append(RegistrySyncViolation(rel_path, e.lineno or 1, "syntax_error", str(e)))
        return violations

    invoke_agent_func = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "invoke_agent":
            invoke_agent_func = node
            break

    if invoke_agent_func is None:
        violations.append(
            RegistrySyncViolation(
                rel_path, 1, "missing_invoke_agent", "invoke_agent function is not defined"
            )
        )
        return violations

    requires_pty_if_node = _find_requires_pty_if_node(invoke_agent_func)
    if requires_pty_if_node is None:
        violations.append(
            RegistrySyncViolation(
                rel_path,
                invoke_agent_func.lineno,
                "invalid_dispatch_ladder",
                "Expected exactly 1 PTY-vs-subprocess dispatch ladder based on requires_pty",
            )
        )
        return violations

    # Check for legacy fallback branches: If statement inside invoke_agent_func
    # referencing CLAUDE_INTERACTIVE/AGY but not inside requires_pty_if_node.body
    allowed_if_nodes = set()
    for stmt in requires_pty_if_node.body:
        for subnode in ast.walk(stmt):
            if isinstance(subnode, ast.If):
                allowed_if_nodes.add(subnode)

    for ast_node in ast.walk(invoke_agent_func):
        if (
            isinstance(ast_node, ast.If)
            and ast_node is not requires_pty_if_node
            and ast_node not in allowed_if_nodes
        ):
            test_src = ast.dump(ast_node.test)
            if "CLAUDE_INTERACTIVE" in test_src or "AGY" in test_src:
                violations.append(
                    RegistrySyncViolation(
                        rel_path,
                        ast_node.lineno,
                        "legacy_fallback_ladder",
                        "Surviving legacy transport fallback branch found in invoke_agent",
                    )
                )

    return violations


def run_audit(package_root: Path) -> list[RegistrySyncViolation]:
    violations: list[RegistrySyncViolation] = []

    builtin_file = package_root / "agents" / "builtin.py"
    if builtin_file.is_file():
        violations.extend(
            audit_builtin_file(builtin_file.read_text(encoding="utf-8"), "ralph/agents/builtin.py")
        )

    registry_file = package_root / "agents" / "registry.py"
    if registry_file.is_file():
        violations.extend(
            audit_registry_file(
                registry_file.read_text(encoding="utf-8"), "ralph/agents/registry.py"
            )
        )

    invoke_file = package_root / "agents" / "invoke" / "__init__.py"
    if invoke_file.is_file():
        violations.extend(
            audit_invoke_file(
                invoke_file.read_text(encoding="utf-8"), "ralph/agents/invoke/__init__.py"
            )
        )

    return violations


def main() -> int:
    package_root = Path(__file__).parent.parent
    violations = run_audit(package_root)
    if violations:
        print("AGENT-REGISTRY-SYNC Violations Found:")
        for v in violations:
            print(f"  {v}")
        return 1

    print("AGENT-REGISTRY-SYNC Audit: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
