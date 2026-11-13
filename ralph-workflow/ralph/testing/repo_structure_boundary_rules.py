from __future__ import annotations

import ast
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = (
    "B4_ALLOWLIST",
    "B5_ALLOWLIST",
    "RULE_PAIRINGS",
    "allowlist_justification",
    "collect_boundary_violations",
    "rule_pairing",
)

RULE_PAIRINGS: Final[tuple[tuple[str, str], ...]] = (
    ("B1", "U6"),
    ("B2", "AC16"),
    ("B3", "AC16"),
    ("B4", "U3a/U10"),
    ("B5", "U6"),
    ("B6", "AC4"),
    ("B7", "U6"),
    ("B8", "AC4"),
    ("B9", "AC3/AC15"),
)
_PAIRING_BY_RULE: Final[Mapping[str, str]] = MappingProxyType(dict(RULE_PAIRINGS))
B4_ALLOWLIST: Final[Mapping[str, str]] = MappingProxyType(
    {
        "ralph/interrupt/dispatcher.py": (
            "fallback raw process-group kill used only when the process manager exposes no "
            "kill_process_group; the managed path is attempted first"
        ),
    }
)
B5_ALLOWLIST: Final[Mapping[str, str]] = MappingProxyType(
    {
        "ralph/pipeline/attempt_runner.py": "canonical invocation boundary",
        "ralph/pipeline/attempt_runner_deps.py": "canonical dependency seam",
        "ralph/pipeline/effect_executor.py": "function definition and invoke dependency",
        "ralph/agents/__init__.py": "public re-export shim",
        "ralph/cli/commands/commit.py": "compatibility re-export shim",
    }
)
_DECISION_NAMES = {
    "end_attempt.py",
    "attribution.py",
    "dead_agent_verdict.py",
    "agent_retry_decision.py",
    "agent_retry_intent.py",
    "retryable_failure.py",
    "attempt_end_accounting.py",
}
_TEXT_NAMES = {"message", "text", "output", "stderr", "stdout", "line"}
_CONTROLLER_PATH = "ralph/recovery/controller.py"
_LEDGER_PATH_PREFIX = "ralph/recovery/agent_failure_ledger"
_ALLOWANCE_SPENT_ATTRIBUTE = "current_allowance_spent"
_SPENT_AGENTS_ATTRIBUTE = "_spent_agents"
_SPENT_AGENTS_MUTATORS = frozenset(
    {"add", "clear", "discard", "pop", "popitem", "remove", "setdefault", "update"}
)
_SPENT_AGENTS_RESET_METHOD = "pop"
_SPENT_AGENTS_WRITE_METHOD = "setdefault"
_SPENT_AGENTS_WRITE_SUFFIX = "add"
_SPENT_AGENTS_WRITE_HOLDER = "note_retry_exhaustion"
_CONSTRUCTOR_NAME = "__init__"
_B3_ALLOWED_PATHS = frozenset(
    {"ralph/recovery/controller.py", "ralph/recovery/unavailability_reason.py"}
)
_ATTEMPT_RUNNER_PATH = "ralph/pipeline/attempt_runner.py"
_MUTABLE_CONSTRUCTORS = frozenset({"dict", "list", "set"})
_B8_DETECTOR_PATHS = frozenset(
    {
        "ralph/agents/invoke/_quota_detection.py",
        "ralph/agents/parsers/content_channel.py",
    }
)
_B8_PROCESS_IMPORT_PREFIX = "ralph.process"
_B8_PROCESS_NAME = "ManagedProcess"
# B9 is an invoke-layer signalling boundary plus a typed-issuer clause; it does
# not make ProcessManager.terminate the sole signalling entry point.
_B9_INVOKE_PREFIXES: Final = ("ralph/agents/invoke/", "ralph/agents/idle_watchdog/")
_B9_SUPERVISOR_PATH: Final = "ralph/agents/invoke/attempt_supervision/attempt_supervisor.py"
_B9_SIGNALING_ATTRIBUTES: Final = frozenset({"terminate", "kill", "killpg", "teardown_subtree"})
_B9_TYPED_ISSUER_ATTRIBUTES: Final = frozenset({"terminate", "kill"})
_B9_MODULE_RECEIVERS: Final = frozenset({"os", "signal"})
_B9_SKIPPED_PREFIXES: Final = ("ralph/process/", "ralph/testing/")


def _b8_violations(relative_path: str, nodes: list[ast.AST]) -> list[str]:
    if relative_path not in _B8_DETECTOR_PATHS:
        return []
    violations: list[str] = []
    for node in nodes:
        reasons: list[str] = []
        if isinstance(node, ast.Import):
            imports_process_layer = any(
                alias.name == _B8_PROCESS_IMPORT_PREFIX
                or alias.name.startswith(f"{_B8_PROCESS_IMPORT_PREFIX}.")
                for alias in node.names
            )
            if imports_process_layer:
                reasons.append("process-layer import in detector module")
        elif isinstance(node, ast.ImportFrom):
            if node.module == _B8_PROCESS_IMPORT_PREFIX or (
                node.module is not None and node.module.startswith(f"{_B8_PROCESS_IMPORT_PREFIX}.")
            ):
                reasons.append("process-layer import in detector module")
        elif (isinstance(node, ast.Name) and node.id == _B8_PROCESS_NAME) or (
            isinstance(node, ast.Attribute) and node.attr == _B8_PROCESS_NAME
        ):
            reasons.append("ManagedProcess reference in detector module")
        violations.extend(_violation(relative_path, node, "B8", reason) for reason in reasons)
    return violations


def _b9_is_signalling_call(func: ast.AST) -> bool:
    if isinstance(func, ast.Name):
        return func.id == "teardown_subtree"
    return isinstance(func, ast.Attribute) and func.attr in _B9_SIGNALING_ATTRIBUTES


def _b9_violations(relative_path: str, nodes: list[ast.AST]) -> list[str]:
    if relative_path == _B9_SUPERVISOR_PATH:
        return []
    in_invoke_layer = relative_path.startswith(_B9_INVOKE_PREFIXES)
    skipped = relative_path.startswith(_B9_SKIPPED_PREFIXES)
    violations: list[str] = []
    for node in nodes:
        if not isinstance(node, ast.Call) or not _b9_is_signalling_call(node.func):
            continue
        if in_invoke_layer:
            violations.append(
                _violation(
                    relative_path,
                    node,
                    "B9",
                    "signalling call outside the attempt supervisor",
                )
            )
            continue
        if skipped or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _B9_TYPED_ISSUER_ATTRIBUTES:
            continue
        receiver = node.func.value
        if isinstance(receiver, ast.Name) and receiver.id in _B9_MODULE_RECEIVERS:
            continue
        if any(keyword.arg == "issuer" for keyword in node.keywords):
            continue
        violations.append(
            _violation(relative_path, node, "B9", "termination call without a typed issuer")
        )
    return violations


def rule_pairing(rule: str) -> str:
    return _PAIRING_BY_RULE[rule]


def _allowlist_entries(rule: str) -> Mapping[str, str] | None:
    if rule == "B4":
        return B4_ALLOWLIST
    if rule == "B5":
        return B5_ALLOWLIST
    return None


def allowlist_justification(rule: str, relative_path: str) -> str | None:
    entries = _allowlist_entries(rule)
    if entries is None:
        return None
    justification = entries.get(relative_path)
    if justification is None:
        return None
    if not justification.strip():
        raise ValueError(f"{rule} allowlist entry without justification: {relative_path}")
    return justification


def _violation(path: str, node: ast.AST, rule: str, reason: str) -> str:
    if rule not in _PAIRING_BY_RULE:
        raise ValueError(f"undeclared boundary rule id: {rule}")
    return f"{path}:{getattr(node, 'lineno', 1)} {rule} {reason}"


def _is_call(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id == name)
        or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
    )


def _decision_path(path: str) -> bool:
    name = PurePosixPath(path).name
    return (
        name in _DECISION_NAMES
        or name.startswith("attempt_outcome")
        or name.startswith("attempt_runner")
    )


def _type_checking_nodes(tree: ast.Module) -> set[int]:
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
            for child in node.body:
                guarded.update(id(item) for item in ast.walk(child))
    return guarded


def _parent_map(tree: ast.Module) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _enclosing_function_name(node: ast.AST, parents: dict[int, ast.AST]) -> str | None:
    current = parents.get(id(node))
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
        current = parents.get(id(current))
    return None


def _b1_violations(relative_path: str, nodes: list[ast.AST]) -> list[str]:
    if relative_path.startswith("ralph/recovery/") or relative_path == _ATTEMPT_RUNNER_PATH:
        return []
    return [
        _violation(relative_path, node, "B1", "RecoveryController construction outside its owners")
        for node in nodes
        if _is_call(node, "RecoveryController")
    ]


def _b2_ledger_violations(relative_path: str, nodes: list[ast.AST]) -> list[str]:
    if relative_path.startswith(_LEDGER_PATH_PREFIX):
        return []
    violations: list[str] = []
    for node in nodes:
        if (isinstance(node, ast.Name) and node.id == _ALLOWANCE_SPENT_ATTRIBUTE) or (
            isinstance(node, ast.Attribute) and node.attr == _ALLOWANCE_SPENT_ATTRIBUTE
        ):
            violations.append(
                _violation(relative_path, node, "B2", "current_allowance_spent outside the ledger")
            )
        elif _is_call(node, "Spent"):
            violations.append(
                _violation(relative_path, node, "B2", "Spent construction outside the ledger")
            )
    return violations


def _spent_agents_attributes(nodes: list[ast.AST]) -> list[ast.Attribute]:
    return [
        node
        for node in nodes
        if isinstance(node, ast.Attribute) and node.attr == _SPENT_AGENTS_ATTRIBUTE
    ]


def _spent_agents_references(nodes: list[ast.AST]) -> list[ast.AST]:
    return [
        node
        for node in nodes
        if (isinstance(node, ast.Attribute) and node.attr == _SPENT_AGENTS_ATTRIBUTE)
        or (isinstance(node, ast.Name) and node.id == _SPENT_AGENTS_ATTRIBUTE)
    ]


def _spent_agents_receiver(node: ast.AST) -> ast.AST | None:
    if isinstance(node, ast.Attribute) and node.attr == _SPENT_AGENTS_ATTRIBUTE:
        return node
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == _SPENT_AGENTS_ATTRIBUTE
    ):
        return node
    return None


def _constructor_binding(
    attributes: list[ast.Attribute], parents: dict[int, ast.AST]
) -> ast.Attribute | None:
    constructor: ast.Attribute | None = None
    for attribute in attributes:
        if not isinstance(attribute.ctx, ast.Store):
            continue
        if _enclosing_function_name(attribute, parents) != _CONSTRUCTOR_NAME:
            continue
        if constructor is None or (attribute.lineno, attribute.col_offset) < (
            constructor.lineno,
            constructor.col_offset,
        ):
            constructor = attribute
    return constructor


def _receiver_chain(node: ast.Call) -> tuple[ast.AST, tuple[str, ...]]:
    methods: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Call):
        if not isinstance(current.func, ast.Attribute):
            break
        methods.append(current.func.attr)
        current = current.func.value
    return current, tuple(methods)


def _is_intermediate_receiver(node: ast.Call, parents: dict[int, ast.AST]) -> bool:
    parent = parents.get(id(node))
    if not (isinstance(parent, ast.Attribute) and parent.value is node):
        return False
    outer = parents.get(id(parent))
    return (
        isinstance(outer, ast.Call)
        and isinstance(outer.func, ast.Attribute)
        and outer.func is parent
    )


def _is_sanctioned_write(
    node: ast.Call, methods: tuple[str, ...], parents: dict[int, ast.AST]
) -> bool:
    if methods != (_SPENT_AGENTS_WRITE_SUFFIX, _SPENT_AGENTS_WRITE_METHOD):
        return False
    return _enclosing_function_name(node, parents) == _SPENT_AGENTS_WRITE_HOLDER


def _b2_mutation_violations(
    relative_path: str,
    nodes: list[ast.AST],
    parents: dict[int, ast.AST],
) -> list[str]:
    violations: list[str] = []
    for node in nodes:
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        root, methods = _receiver_chain(node)
        if _spent_agents_receiver(root) is None:
            continue
        mutators = {method for method in methods if method in _SPENT_AGENTS_MUTATORS}
        if not mutators or mutators == {_SPENT_AGENTS_RESET_METHOD}:
            continue
        if _is_intermediate_receiver(node, parents):
            continue
        if _is_sanctioned_write(node, methods, parents):
            continue
        violations.append(
            _violation(relative_path, node, "B2", "unsanctioned _spent_agents mutation")
        )
    return violations


def _b2_controller_violations(
    relative_path: str,
    nodes: list[ast.AST],
    parents: dict[int, ast.AST],
) -> list[str]:
    attributes = _spent_agents_attributes(nodes)
    constructor = _constructor_binding(attributes, parents)
    violations: list[str] = []
    for attribute in attributes:
        parent = parents.get(id(attribute))
        if isinstance(attribute.ctx, ast.Store):
            if attribute is not constructor:
                violations.append(
                    _violation(
                        relative_path,
                        attribute,
                        "B2",
                        "unsanctioned _spent_agents assignment",
                    )
                )
            continue
        if isinstance(attribute.ctx, ast.Del):
            violations.append(
                _violation(
                    relative_path,
                    attribute,
                    "B2",
                    "unsanctioned _spent_agents deletion",
                )
            )
            continue
        if (
            isinstance(parent, ast.Subscript)
            and parent.value is attribute
            and isinstance(parent.ctx, (ast.Store, ast.Del))
        ):
            violations.append(
                _violation(
                    relative_path,
                    attribute,
                    "B2",
                    "unsanctioned _spent_agents subscript write",
                )
            )
    violations.extend(_b2_mutation_violations(relative_path, nodes, parents))
    return violations


def _b2_violations(
    relative_path: str, nodes: list[ast.AST], parents: dict[int, ast.AST]
) -> list[str]:
    violations = _b2_ledger_violations(relative_path, nodes)
    if relative_path == _CONTROLLER_PATH:
        violations.extend(_b2_controller_violations(relative_path, nodes, parents))
    else:
        violations.extend(
            _violation(relative_path, node, "B2", "_spent_agents outside the controller")
            for node in _spent_agents_references(nodes)
        )
    return violations


def _b3_violations(relative_path: str, nodes: list[ast.AST]) -> list[str]:
    if relative_path in _B3_ALLOWED_PATHS:
        return []
    violations: list[str] = []
    for node in nodes:
        if _is_call(node, "mark_unavailable") or _is_call(node, "record_confirmed_unavailability"):
            violations.append(
                _violation(
                    relative_path,
                    node,
                    "B3",
                    "unavailability mutation outside the controller",
                )
            )
        elif isinstance(node, ast.Attribute) and node.attr == "DEAD_AGENT_NO_PROGRESS":
            violations.append(
                _violation(relative_path, node, "B3", "dead-agent reason outside its owners")
            )
    return violations


def _b4_violations(relative_path: str, nodes: list[ast.AST]) -> list[str]:
    if relative_path.startswith("ralph/process/") or (
        allowlist_justification("B4", relative_path) is not None
    ):
        return []
    violations: list[str] = []
    for node in nodes:
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        receiver = node.func.value
        if isinstance(receiver, ast.Name) and receiver.id == "os" and node.func.attr == "killpg":
            violations.append(_violation(relative_path, node, "B4", "raw process-group signal"))
        elif (
            isinstance(receiver, ast.Name)
            and receiver.id == "os"
            and node.func.attr == "kill"
            and len(node.args) > 1
            and not (isinstance(node.args[1], ast.Constant) and node.args[1].value == 0)
        ):
            violations.append(_violation(relative_path, node, "B4", "raw non-zero process signal"))
        elif node.func.attr == "send_signal":
            violations.append(_violation(relative_path, node, "B4", "raw process signal"))
        elif (
            node.func.attr in {"terminate", "kill"}
            and isinstance(receiver, ast.Name)
            and "psutil" in receiver.id.lower()
        ):
            violations.append(_violation(relative_path, node, "B4", "raw psutil termination"))
    return violations


def _b5_import_from_bindings(
    node: ast.ImportFrom,
    functions: set[str],
    modules: set[str],
    imports: list[ast.AST],
) -> None:
    if node.module in {"ralph.pipeline.effect_executor", "ralph.agents.invoke"}:
        target = "execute_agent_effect" if "effect_executor" in node.module else "invoke_agent"
        for alias in node.names:
            if alias.name == target:
                functions.add(alias.asname or alias.name)
                imports.append(node)
    if node.module == "ralph.agents":
        for alias in node.names:
            if alias.name == "invoke_agent":
                functions.add(alias.asname or alias.name)
                imports.append(node)
    if node.module == "ralph.pipeline":
        for alias in node.names:
            if alias.name == "effect_executor":
                modules.add(alias.asname or alias.name)
                imports.append(node)


def _b5_bindings(tree: ast.Module, guarded: set[int]) -> tuple[set[str], set[str], list[ast.AST]]:
    functions: set[str] = set()
    modules: set[str] = set()
    imports: list[ast.AST] = []
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.ImportFrom):
            _b5_import_from_bindings(node, functions, modules, imports)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "ralph.pipeline.effect_executor":
                    modules.add(alias.asname or alias.name.split(".")[0])
                    imports.append(node)
    return functions, modules, imports


def _b5_violations(relative_path: str, tree: ast.Module, nodes: list[ast.AST]) -> list[str]:
    if allowlist_justification("B5", relative_path) is not None or relative_path.startswith(
        "ralph/agents/invoke/"
    ):
        return []
    guarded = _type_checking_nodes(tree)
    _functions, modules, imports = _b5_bindings(tree, guarded)
    violations = [
        _violation(relative_path, node, "B5", "low-level agent function import outside allowlist")
        for node in imports
    ]
    for node in nodes:
        if id(node) in guarded:
            continue
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in modules
            and node.attr == "execute_agent_effect"
        ):
            violations.append(
                _violation(
                    relative_path,
                    node,
                    "B5",
                    "low-level agent function through module alias",
                )
            )
    return violations


def _b6_violations(relative_path: str, nodes: list[ast.AST]) -> list[str]:
    violations: list[str] = []
    for node in nodes:
        if (isinstance(node, ast.Import) and any(alias.name == "re" for alias in node.names)) or (
            isinstance(node, ast.ImportFrom) and node.module == "re"
        ):
            violations.append(
                _violation(relative_path, node, "B6", "regular-expression import in decision layer")
            )
        elif isinstance(node, ast.Attribute) and node.attr == "args":
            violations.append(
                _violation(relative_path, node, "B6", "exception args read in decision layer")
            )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "str"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and (node.args[0].id in {"exc", "err", "error"} or "exception" in node.args[0].id)
        ):
            violations.append(
                _violation(
                    relative_path, node, "B6", "exception converted to text in decision layer"
                )
            )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"lower", "casefold", "startswith", "endswith", "find"}
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in _TEXT_NAMES
        ):
            violations.append(
                _violation(relative_path, node, "B6", "message text inspected in decision layer")
            )
        elif (
            isinstance(node, ast.Compare)
            and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
            and any(
                isinstance(item, ast.Name) and item.id in _TEXT_NAMES for item in node.comparators
            )
        ):
            violations.append(
                _violation(relative_path, node, "B6", "message membership test in decision layer")
            )
    return violations


def _b7_violations(relative_path: str, tree: ast.Module, nodes: list[ast.AST]) -> list[str]:
    violations: list[str] = []
    for node in nodes:
        if isinstance(node, ast.Global):
            violations.append(
                _violation(relative_path, node, "B7", "global statement in decision layer")
            )
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and node.attr in {"environ", "getenv"}
        ) or _is_call(node, "getenv"):
            violations.append(
                _violation(relative_path, node, "B7", "environment read in decision layer")
            )
    for node in tree.body:
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        mutable = isinstance(value, (ast.Dict, ast.List, ast.Set)) or (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in _MUTABLE_CONSTRUCTORS
        )
        if mutable:
            violations.append(
                _violation(
                    relative_path, node, "B7", "mutable module-level state in decision layer"
                )
            )
    return violations


def _decision_violations(relative_path: str, tree: ast.Module, nodes: list[ast.AST]) -> list[str]:
    if not _decision_path(relative_path):
        return []
    return _b6_violations(relative_path, nodes) + _b7_violations(relative_path, tree, nodes)


def collect_boundary_violations(relative_path: str, source: str) -> list[str]:
    if relative_path.startswith("ralph/testing/"):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    nodes = list(ast.walk(tree))
    parents = _parent_map(tree)
    return (
        _b1_violations(relative_path, nodes)
        + _b2_violations(relative_path, nodes, parents)
        + _b3_violations(relative_path, nodes)
        + _b4_violations(relative_path, nodes)
        + _b5_violations(relative_path, tree, nodes)
        + _decision_violations(relative_path, tree, nodes)
        + _b8_violations(relative_path, nodes)
        + _b9_violations(relative_path, nodes)
    )
