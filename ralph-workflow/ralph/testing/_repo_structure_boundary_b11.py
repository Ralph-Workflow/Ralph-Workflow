from __future__ import annotations

import ast
from typing import Final

from ralph.testing._repo_structure_boundary_common import (
    _call_name,
    _target_root_names,
    _violation,
)

_B11_DISPATCH_CALL_NAMES: Final = frozenset({"invoke", "invoke_agent"})
_B11_AGENT_TYPE_NAMES: Final = frozenset({"InvokeAgentEffect", "AgentConfig"})
_B11_AGENT_TYPE_MARKERS: Final = ("AgentEffect", "AgentConfig")
_B11_AGENT_ATTRIBUTE_NAMES: Final = frozenset({"agent_config"})
_B11_UNKNOWN_ANNOTATION_NAMES: Final = frozenset({"object", "Any"})
_B11_EXCLUDED_PREFIXES: Final = ("ralph/testing/", "ralph/pipeline/effects/")
_B11_FUNCTION_TYPES: Final = (ast.FunctionDef, ast.AsyncFunctionDef)
B11_ALLOWLIST: Final[tuple[tuple[str, str, int, str], ...]] = (
    (
        "ralph/_session_runtime_deps.py",
        "_invoke_agent",
        79,
        "session runtime raw agent invocation owned by the registered session runtime route",
    ),
    (
        "ralph/_session_runtime_runtime.py",
        "_session_invoke",
        296,
        "session runtime dispatch through the deps owned by the registered session runtime route",
    ),
    (
        "ralph/pipeline/attempt_runner.py",
        "run_agent_attempt",
        237,
        "canonical attempt dispatch inside the registered attempt runner",
    ),
    (
        "ralph/pipeline/attempt_runner_deps.py",
        "raw_attempt_invoke",
        144,
        "raw attempt invocation seam reached only through the registered attempt runner",
    ),
    (
        "ralph/pipeline/effect_executor.py",
        "_consume_attempt_output",
        1552,
        "effect executor dispatch owned by the registered effect route",
    ),
    (
        "ralph/pipeline/plumbing/commit_plumbing.py",
        "_attempt_deps",
        197,
        "commit attempt invoke seam reached only through the registered commit route",
    ),
    (
        "ralph/pipeline/plumbing/commit_plumbing.py",
        "_attempt_deps",
        205,
        "commit attempt invoke seam reached only through the registered commit route",
    ),
    (
        "ralph/pipeline/plumbing/commit_plumbing.py",
        "invoke_commit_agent_attempt",
        970,
        "commit module agent invocation owned by the registered commit route",
    ),
)


def b11_allowlist_justification(
    module_path: str, function_name: str | None, call_line: int
) -> str | None:
    for path, function, line, justification in B11_ALLOWLIST:
        if path != module_path or function != function_name or line != call_line:
            continue
        if not justification.strip():
            raise ValueError(f"B11 allowlist entry without justification: {path}:{line}")
        return justification
    return None


def _b11_names_agent_type(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in _B11_AGENT_TYPE_NAMES:
            return True
        if isinstance(child, ast.Attribute) and child.attr in _B11_AGENT_TYPE_NAMES:
            return True
        if isinstance(child, ast.Name) and any(
            marker in child.id for marker in _B11_AGENT_TYPE_MARKERS
        ):
            return True
    return False


def _b11_annotation_agentness(annotation: ast.AST | None) -> str:
    if annotation is None:
        return "unknown"
    if _b11_names_agent_type(annotation):
        return "agent"
    names = {child.id for child in ast.walk(annotation) if isinstance(child, ast.Name)}
    if names & _B11_UNKNOWN_ANNOTATION_NAMES:
        return "unknown"
    return "other"


def _b11_constructor_agentness(name: str | None) -> str:
    if name is None:
        return "unknown"
    if name in _B11_AGENT_TYPE_NAMES or any(marker in name for marker in _B11_AGENT_TYPE_MARKERS):
        return "agent"
    if "Agent" in name:
        return "unknown"
    return "other"


def _b11_value_agentness(value: ast.AST | None) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, ast.Call):
        return _b11_constructor_agentness(_call_name(value.func))
    return "unknown"


_B11Function = ast.FunctionDef | ast.AsyncFunctionDef


def _b11_function_owners(tree: ast.Module) -> dict[int, _B11Function | None]:
    owners: dict[int, _B11Function | None] = {}

    def visit(node: ast.AST, current: _B11Function | None) -> None:
        owners[id(node)] = current
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _B11_FUNCTION_TYPES):
                owners[id(child)] = current
                for grandchild in ast.iter_child_nodes(child):
                    visit(grandchild, child)
            else:
                visit(child, current)

    visit(tree, None)
    return owners


def _b11_parameter_agentness(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, str]:
    arguments = node.args
    bindings: dict[str, str] = {}
    for parameter in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
        bindings[parameter.arg] = _b11_annotation_agentness(parameter.annotation)
    if arguments.vararg is not None:
        bindings[arguments.vararg.arg] = "unknown"
    if arguments.kwarg is not None:
        bindings[arguments.kwarg.arg] = "unknown"
    return bindings


def _b11_local_bindings(
    tree: ast.Module, owners: dict[int, _B11Function | None]
) -> tuple[
    dict[int, dict[str, str]],
    dict[int, dict[str, ast.FunctionDef | ast.AsyncFunctionDef]],
]:
    bindings: dict[int, dict[str, str]] = {}
    definitions: dict[int, dict[str, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, _B11_FUNCTION_TYPES):
            owner = owners.get(id(node))
            definition_scope = definitions.setdefault(id(owner) if owner is not None else 0, {})
            definition_scope[node.name] = node
            continue
        if not isinstance(node, ast.Assign) and not isinstance(node, ast.AnnAssign):
            continue
        owner = owners.get(id(node))
        binding_scope = bindings.setdefault(id(owner) if owner is not None else 0, {})
        value = node.value
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        agentness = _b11_value_agentness(value)
        for target in targets:
            for name in _target_root_names(target):
                binding_scope[name] = agentness
    return bindings, definitions


def _b11_parameter_annotation(
    node: ast.FunctionDef | ast.AsyncFunctionDef, name: str
) -> ast.AST | None:
    arguments = node.args
    for parameter in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
        if parameter.arg == name:
            return parameter.annotation
    return None


def _b11_first_parameter_agentness(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    arguments = node.args
    for parameter in (*arguments.posonlyargs, *arguments.args):
        return _b11_annotation_agentness(parameter.annotation)
    return "unknown"


def _b11_callable_first_argument(annotation: ast.AST | None) -> ast.AST | None:
    if annotation is None or not isinstance(annotation, ast.Subscript):
        return None
    if _call_name(annotation.value) != "Callable":
        return None
    subscript = annotation.slice
    if not isinstance(subscript, ast.Tuple) or not subscript.elts:
        return None
    first = subscript.elts[0]
    if isinstance(first, ast.List):
        return first.elts[0] if first.elts else None
    return first


def _b11_callee_agentness(
    func: ast.AST,
    owner: _B11Function | None,
    owners: dict[int, _B11Function | None],
    definitions: dict[int, dict[str, ast.FunctionDef | ast.AsyncFunctionDef]],
) -> str:
    if not isinstance(func, ast.Name):
        return "unknown"
    current = owner
    while True:
        scope_id = id(current) if current is not None else 0
        definition = definitions.get(scope_id, {}).get(func.id)
        if definition is not None:
            return _b11_first_parameter_agentness(definition)
        if current is not None:
            annotation = _b11_parameter_annotation(current, func.id)
            if annotation is not None:
                first = _b11_callable_first_argument(annotation)
                if first is not None:
                    return _b11_annotation_agentness(first)
                return "unknown"
        if current is None:
            return "unknown"
        current = owners.get(id(current))


def _b11_lookup_name(
    name: str,
    owner: _B11Function | None,
    owners: dict[int, _B11Function | None],
    local_bindings: dict[int, dict[str, str]],
) -> str:
    current = owner
    while True:
        scope = local_bindings.get(id(current) if current is not None else 0, {})
        if name in scope:
            return scope[name]
        if current is not None:
            parameters = _b11_parameter_agentness(current)
            if name in parameters:
                return parameters[name]
        if current is None:
            return "unknown"
        current = owners.get(id(current))


def _b11_argument_agentness(
    argument: ast.AST,
    owner: _B11Function | None,
    owners: dict[int, _B11Function | None],
    local_bindings: dict[int, dict[str, str]],
) -> str:
    if isinstance(argument, ast.Name):
        return _b11_lookup_name(argument.id, owner, owners, local_bindings)
    if isinstance(argument, ast.Attribute):
        if argument.attr in _B11_AGENT_ATTRIBUTE_NAMES or any(
            marker in argument.attr for marker in _B11_AGENT_TYPE_MARKERS
        ):
            return "agent"
        return "unknown"
    if isinstance(argument, ast.Constant):
        return "other"
    if isinstance(argument, ast.Call):
        return _b11_constructor_agentness(_call_name(argument.func))
    return "unknown"


def _b11_violations(
    relative_path: str,
    tree: ast.Module,
    source: str,
    nodes: list[ast.AST],
) -> list[str]:
    if not relative_path.startswith("ralph/"):
        return []
    if relative_path.startswith(_B11_EXCLUDED_PREFIXES):
        return []
    if "invoke" not in source:
        return []
    owners = _b11_function_owners(tree)
    local_bindings, definitions = _b11_local_bindings(tree, owners)
    violations: list[str] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node.func) not in _B11_DISPATCH_CALL_NAMES:
            continue
        if not node.args:
            continue
        owner = owners.get(id(node))
        callee = _b11_callee_agentness(node.func, owner, owners, definitions)
        if callee == "other":
            continue
        argument = node.args[0]
        agentness = _b11_argument_agentness(argument, owner, owners, local_bindings)
        if callee != "agent" and agentness == "other":
            continue
        function_name = owner.name if isinstance(owner, _B11_FUNCTION_TYPES) else None
        if b11_allowlist_justification(relative_path, function_name, node.lineno) is not None:
            continue
        violations.append(
            _violation(
                relative_path,
                node,
                "B11",
                "agent dispatch outside the entry-point registry allowlist",
            )
        )
    return violations
