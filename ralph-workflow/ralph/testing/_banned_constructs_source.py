from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.testing._banned_constructs_model import (
    CONFIG_ENV_MODULE,
    Finding,
    _AuditInputError,
    _label,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_DANGEROUS_CALLS = {
    "eval": "S307",
    "exec": "S102",
    "compile": "BANNED-CALL",
    "__import__": "S403",
    "globals": "BANNED-NAMESPACE",
    "locals": "BANNED-NAMESPACE",
    "vars": "BANNED-NAMESPACE",
}
_FORBIDDEN_IMPORT_ROOTS = {
    "ctypes": "S401",
    "cffi": "BANNED-IMPORT",
    "pickle": "S301",
    "marshal": "S302",
}
_DYNAMIC_METHODS = frozenset({"__getattr__", "__getattribute__", "__setattr__"})
_SYS_PATH_MUTATORS = frozenset({"append", "clear", "extend", "insert", "pop", "remove"})
_THREAD_PRIMITIVES = frozenset(
    {"Barrier", "Condition", "Event", "Lock", "Queue", "RLock", "Semaphore"}
)
_MUTABLE_FACTORIES = frozenset(
    {"Counter", "OrderedDict", "bytearray", "defaultdict", "deque", "dict", "list", "set"}
)
_DICT_MUTATORS = frozenset({"clear", "pop", "popitem", "setdefault", "update"})
_ALL_MUTATORS = frozenset(
    {"append", "clear", "extend", "insert", "pop", "popitem", "remove", "reverse", "sort"}
)
_TYPE_IGNORE_RE = re.compile(r"#\s*type\s*:\s*ignore(?:\s*\[[^]]*\])?", re.IGNORECASE)
_NOQA_RE = re.compile(r"#\s*noqa(?:\s*:|\s|$)", re.IGNORECASE)
_ATTRIBUTE_ARGUMENT_COUNT = 2
_SETATTR_ARGUMENT_COUNT = 3
_TYPE_FACTORY_ARGUMENT_COUNT = 3
_DISPATCH_CHAIN_LENGTH = 2


@dataclass(frozen=True, slots=True)
class _SourceUnit:
    path: Path
    label: str
    module: str
    package: str
    source: str
    tree: ast.Module


@dataclass(frozen=True, slots=True)
class _ClassContext:
    slots: bool


def _literal_string(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _constant_true(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _label_parts(label: str) -> tuple[list[str], bool]:
    parts = list(Path(label).parts)
    for root in ("ralph", "tests"):
        if root in parts:
            parts = parts[parts.index(root) :]
            break
    if not parts:
        return [], False
    is_package = Path(parts[-1]).stem == "__init__"
    parts[-1] = Path(parts[-1]).stem
    if is_package:
        parts.pop()
    return parts, is_package


def _module_from_label(label: str) -> str:
    parts, _is_package = _label_parts(label)
    return ".".join(parts)


def _package_from_label(label: str) -> str:
    parts, is_package = _label_parts(label)
    if is_package:
        return ".".join(parts)
    return ".".join(parts[:-1])


def _is_production(label: str) -> bool:
    return "ralph" in Path(label).parts


def _is_config_env_module(module: str) -> bool:
    return module == CONFIG_ENV_MODULE


def _resolve_relative_module(unit: _SourceUnit, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = unit.package.split(".") if unit.package else []
    keep = max(0, len(package) - node.level + 1)
    parts = package[:keep]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)


def _import_bindings(unit: _SourceUnit) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is not None:
                    bindings[alias.asname] = alias.name
                else:
                    bindings[alias.name.split(".")[0]] = alias.name.split(".")[0]
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        module = _resolve_relative_module(unit, node)
        for alias in node.names:
            if alias.name == "*":
                continue
            local = alias.asname if alias.asname is not None else alias.name
            bindings[local] = f"{module}.{alias.name}" if module else alias.name
    return bindings


def _qualified_name(node: ast.expr, imports: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return imports.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value, imports)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Subscript):
        return _qualified_name(node.value, imports)
    return ""


def _class_index(units: Sequence[_SourceUnit]) -> frozenset[str]:
    names: set[str] = set()
    for unit in units:
        for statement in unit.tree.body:
            if isinstance(statement, ast.ClassDef):
                names.add(f"{unit.module}.{statement.name}")
    return frozenset(names)


def _parent_map(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _target_contains(target: ast.expr, node: ast.AST) -> bool:
    return target is node or any(descendant is node for descendant in ast.walk(target))


def _is_write_access(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, ast.Assign):
            return any(_target_contains(target, node) for target in parent.targets)
        if isinstance(parent, ast.AnnAssign | ast.AugAssign):
            return _target_contains(parent.target, node)
        if isinstance(parent, ast.Delete):
            return any(_target_contains(target, node) for target in parent.targets)
        if not isinstance(parent, ast.Attribute | ast.Subscript):
            return False
        current = parent
    return False


def _assignment_targets(statement: ast.Assign | ast.AnnAssign) -> tuple[ast.expr, ...]:
    if isinstance(statement, ast.Assign):
        return tuple(statement.targets)
    return (statement.target,)


def _mutable_value(node: ast.expr | None) -> bool:
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    if not isinstance(node, ast.Call):
        return False
    leaf = _qualified_name(node.func, {}).rsplit(".", maxsplit=1)[-1]
    return leaf in _MUTABLE_FACTORIES


def _module_mutables(tree: ast.Module) -> dict[str, int]:
    found: dict[str, int] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign | ast.AnnAssign):
            continue
        if not _mutable_value(statement.value):
            continue
        for target in _assignment_targets(statement):
            if isinstance(target, ast.Name):
                found[target.id] = statement.lineno
    return found


def _module_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        statement.name: statement
        for statement in tree.body
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _loads_name(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(child, ast.Name) and child.id == name and isinstance(child.ctx, ast.Load)
        for child in ast.walk(node)
    )


def _node_line(node: ast.AST) -> int:
    if isinstance(node, (ast.stmt, ast.expr, ast.arg, ast.keyword, ast.ExceptHandler)):
        return node.lineno
    return 1


class _SourceAuditor(ast.NodeVisitor):
    __slots__ = (
        "class_stack",
        "classes",
        "findings",
        "function_depth",
        "imports",
        "module_functions",
        "module_mutables",
        "parents",
        "pass_through_stack",
        "unit",
    )

    def __init__(self, unit: _SourceUnit, classes: frozenset[str]) -> None:
        self.unit = unit
        self.classes = classes
        self.imports = _import_bindings(unit)
        self.parents = _parent_map(unit.tree)
        self.findings: list[Finding] = []
        self.class_stack: list[_ClassContext] = []
        self.function_depth = 0
        self.pass_through_stack: list[tuple[str | None, str | None]] = []
        self.module_mutables = _module_mutables(unit.tree)
        self.module_functions = _module_functions(unit.tree)

    def add(self, node: ast.AST, construct: str, code: str, rule_id: str | None = None) -> None:
        self.findings.append(
            Finding(
                self.unit.label,
                _node_line(node),
                construct,
                code,
                rule_id if rule_id is not None else code,
            )
        )

    def _qualified(self, node: ast.expr) -> str:
        return _qualified_name(node, self.imports)

    def _is_imported_root(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.imports
        if isinstance(node, ast.Attribute):
            return self._is_imported_root(node.value)
        return False

    def _slots_in_class(self, node: ast.ClassDef) -> bool:
        for statement in node.body:
            if not isinstance(statement, ast.Assign | ast.AnnAssign):
                continue
            for target in _assignment_targets(statement):
                if isinstance(target, ast.Name) and target.id == "__slots__":
                    return True
        return False

    def _cross_module_base(self, base: ast.expr) -> str | None:
        qualified = self._qualified(base)
        if qualified not in self.classes:
            return None
        module = qualified.rsplit(".", maxsplit=1)[0]
        if module == self.unit.module:
            return None
        return qualified

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._check_mutable_defaults(node)
        self.function_depth += 1
        vararg = node.args.vararg.arg if node.args.vararg is not None else None
        kwarg = node.args.kwarg.arg if node.args.kwarg is not None else None
        self.pass_through_stack.append((vararg, kwarg))
        self.generic_visit(node)
        self.pass_through_stack.pop()
        self.function_depth -= 1

    def _check_mutable_defaults(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        defaults: list[ast.expr] = list(node.args.defaults)
        defaults.extend(default for default in node.args.kw_defaults if default is not None)
        for default in defaults:
            if _mutable_value(default):
                self.add(default, "mutable default argument", "MUTABLE-DEFAULT", "B006")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for keyword in node.keywords:
            if keyword.arg == "metaclass":
                self.add(keyword, "metaclass= class definition", "METACLASS")
        for base in node.bases:
            cross_module = self._cross_module_base(base)
            if cross_module is not None:
                self.add(
                    base,
                    f"cross-module inheritance from {cross_module}",
                    "CROSS-MODULE-INHERITANCE",
                )
        for statement in node.body:
            if not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if statement.name in _DYNAMIC_METHODS:
                self.add(statement, f"{statement.name} method override", "DYNAMIC-METHOD")
            if statement.name == "__del__":
                self.add(statement, "__del__ method", "FINALIZER")
        self.class_stack.append(_ClassContext(self._slots_in_class(node)))
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in _FORBIDDEN_IMPORT_ROOTS:
                self.add(
                    node,
                    f"{root} import",
                    "BANNED-IMPORT",
                    _FORBIDDEN_IMPORT_ROOTS[root],
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module if node.module is not None else ""
        root = module.split(".")[0]
        if any(alias.name == "*" for alias in node.names):
            self.add(node, "star import", "STAR-IMPORT", "F403")
        if root in _FORBIDDEN_IMPORT_ROOTS:
            self.add(
                node,
                f"{root} import",
                "BANNED-IMPORT",
                _FORBIDDEN_IMPORT_ROOTS[root],
            )
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.add(node, "global declaration", "GLOBAL-STATE", "PLW0603")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.add(node, "nonlocal declaration", "GLOBAL-STATE", "PLW0642")

    def visit_Assert(self, node: ast.Assert) -> None:
        if _is_production(self.unit.label):
            self.add(node, "runtime assert", "RUNTIME-ASSERT", "S101")
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.add(node, "bare except", "BROAD-EXCEPT", "E722")
        broad = self._broad_exception(node.type)
        if broad is not None:
            raises = any(
                isinstance(child, ast.Raise)
                for statement in node.body
                for child in ast.walk(statement)
            )
            if not raises:
                self.add(node, f"swallowed {broad} exception", "BROAD-EXCEPT", "BLE001")
        self.generic_visit(node)

    def _broad_exception(self, node: ast.expr | None) -> str | None:
        if isinstance(node, ast.Name) and node.id in {"BaseException", "Exception"}:
            return node.id
        if isinstance(node, ast.Tuple):
            for element in node.elts:
                if isinstance(element, ast.Name) and element.id in {"BaseException", "Exception"}:
                    return element.id
        return None

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Lambda) and any(
            isinstance(target, ast.Name) for target in node.targets
        ):
            self.add(node, "lambda assigned to a name", "ASSIGNED-LAMBDA", "E731")
        for target in node.targets:
            self._check_assignment_target(target, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.value, ast.Lambda) and isinstance(node.target, ast.Name):
            self.add(node, "lambda assigned to a name", "ASSIGNED-LAMBDA", "E731")
        self._check_assignment_target(node.target, node)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_assignment_target(node.target, node)
        if self._is_all_expression(node.target):
            self.add(node, "__all__ mutation", "ALL-MUTATION")
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            if self._is_all_expression(target):
                self.add(target, "__all__ mutation", "ALL-MUTATION")
            self._check_assignment_target(target, node)
        self.generic_visit(node)

    def _check_assignment_target(self, target: ast.expr, node: ast.AST) -> None:
        if isinstance(target, ast.Attribute) and self._is_imported_root(target.value):
            self.add(target, "runtime monkeypatch of imported object", "MONKEYPATCH")
        if isinstance(target, ast.Attribute) and target.attr == "__all__":
            self.add(target, "__all__ mutation", "ALL-MUTATION")
        if isinstance(target, ast.Name) and target.id == "__metaclass__":
            self.add(target, "__metaclass__ assignment", "METACLASS")
        if self._is_sys_path_expression(target):
            self.add(target, "sys.path edit", "SYS-PATH")
        if self._is_dunder_dict_expression(target):
            self.add(target, "dynamic __dict__ attribute mutation", "DYNAMIC-INSTANCE-ATTRIBUTE")
            return
        if self._is_dynamic_instance_attribute(target):
            self.add(target, "dynamic instance attribute creation", "DYNAMIC-INSTANCE-ATTRIBUTE")
        if self.function_depth == 0:
            self._check_thread_primitive(node)

    def _is_dynamic_instance_attribute(self, target: ast.expr) -> bool:
        if not self.class_stack or self.class_stack[-1].slots:
            return False
        return (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id in {"cls", "self"}
        )

    def _is_sys_path_expression(self, node: ast.expr) -> bool:
        current: ast.expr = node
        while isinstance(current, ast.Subscript | ast.Attribute):
            if isinstance(current, ast.Subscript):
                current = current.value
                continue
            if self._qualified(current) == "sys.path":
                return True
            current = current.value
        return self._qualified(current) == "sys.path"

    def _is_dunder_dict_expression(self, node: ast.expr) -> bool:
        current: ast.expr = node
        while isinstance(current, ast.Subscript):
            current = current.value
        return isinstance(current, ast.Attribute) and current.attr == "__dict__"

    def _is_all_expression(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id == "__all__"
        if isinstance(node, ast.Attribute):
            return node.attr == "__all__"
        if isinstance(node, ast.Subscript):
            return self._is_all_expression(node.value)
        return False

    def _check_thread_primitive(self, node: ast.AST) -> None:
        value: ast.expr | None = None
        if isinstance(node, ast.Assign | ast.AnnAssign):
            value = node.value
        if not isinstance(value, ast.Call):
            return
        qualified = self._qualified(value.func)
        leaf = qualified.rsplit(".", maxsplit=1)[-1]
        if leaf in _THREAD_PRIMITIVES and (
            qualified.startswith("threading.") or qualified == "queue.Queue"
        ):
            self.add(node, f"module-level shared {leaf}", "THREAD-STATE")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        qualified = self._qualified(node)
        if qualified == "typing.Any":
            self.add(node, "typing.Any usage", "TYPING-ANY", "ANN401")
        if qualified.startswith("inspect.") and _is_production(self.unit.label):
            self.add(node, "inspect-based reflection", "INSPECT-REFLECTION")
        if (
            qualified in {"os.environ", "os.getenv"}
            and not _is_config_env_module(self.unit.module)
            and not _is_write_access(node, self.parents)
        ):
            self.add(node, "os.environ read", "OS-ENVIRON")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        qualified = self.imports.get(node.id, "")
        if qualified == "typing.Any" and isinstance(node.ctx, ast.Load):
            self.add(node, "typing.Any usage", "TYPING-ANY", "ANN401")
        if (
            qualified in {"os.environ", "os.getenv"}
            and isinstance(node.ctx, ast.Load)
            and not _is_config_env_module(self.unit.module)
            and not _is_write_access(node, self.parents)
        ):
            self.add(node, "os.environ read", "OS-ENVIRON")

    def visit_Call(self, node: ast.Call) -> None:
        qualified = self._qualified(node.func)
        self._check_call_escape_hatches(node, qualified)
        self._check_attribute_calls(node, qualified)
        self._check_monkeypatch(node, qualified)
        self._check_dynamic_instance_call(node, qualified)
        self._check_blind_pass_through(node)
        if qualified.startswith("subprocess.") and any(
            keyword.arg == "shell" and _constant_true(keyword.value) for keyword in node.keywords
        ):
            self.add(node, "subprocess shell=True", "SHELL-TRUE", "S602")
        if self._is_sys_path_mutation(node):
            self.add(node, "sys.path edit", "SYS-PATH")
        if self._is_all_mutation(node):
            self.add(node, "__all__ mutation", "ALL-MUTATION")
        if self._print_is_banned(qualified):
            self.add(node, "print in library code", "PRINT", "T201")
        if _is_production(self.unit.label) and qualified == "time.sleep":
            self.add(node, "time.sleep in production code", "SLEEP")
        if self._is_dunder_dict_mutation(node):
            self.add(node, "dynamic __dict__ attribute mutation", "DYNAMIC-INSTANCE-ATTRIBUTE")
        self._check_thread_target(node, qualified)
        self.generic_visit(node)

    def _check_call_escape_hatches(self, node: ast.Call, qualified: str) -> None:
        builtin = qualified.removeprefix("builtins.")
        if qualified in {builtin, f"builtins.{builtin}"} and builtin in _DANGEROUS_CALLS:
            self.add(node, f"{builtin} call", "BANNED-CALL", _DANGEROUS_CALLS[builtin])
        if qualified in {"importlib.import_module", "importlib.__import__"}:
            self.add(node, "dynamic importlib loading", "DYNAMIC-IMPORT")
        if qualified == "typing.cast":
            self.add(node, "typing.cast call", "TYPING-CAST", "TC006")
        if (
            qualified in {"type", "builtins.type"}
            and len(node.args) == _TYPE_FACTORY_ARGUMENT_COUNT
        ):
            self.add(node, "three-argument type call", "TYPE-FACTORY")

    def _check_attribute_calls(self, node: ast.Call, qualified: str) -> None:
        attribute_counts = {
            "delattr": _ATTRIBUTE_ARGUMENT_COUNT,
            "getattr": _ATTRIBUTE_ARGUMENT_COUNT,
            "setattr": _SETATTR_ARGUMENT_COUNT,
        }
        builtin = qualified.removeprefix("builtins.")
        count = attribute_counts.get(builtin)
        if count is None or qualified not in {builtin, f"builtins.{builtin}"}:
            return
        if len(node.args) >= count and not _literal_string(node.args[1]):
            self.add(node, f"non-literal {builtin}", "DYNAMIC-ATTRIBUTE")

    def _check_monkeypatch(self, node: ast.Call, qualified: str) -> None:
        patch_qualified = qualified in {
            "mock.patch",
            "patch",
            "unittest.mock.patch",
        } or qualified.startswith("unittest.mock.patch.")
        if patch_qualified:
            self.add(node, "runtime monkeypatch via patch", "MONKEYPATCH")
        if not isinstance(node.func, ast.Attribute):
            return
        receiver = node.func.value
        leaf = node.func.attr
        if (
            isinstance(receiver, ast.Name)
            and receiver.id in {"mocker", "monkeypatch"}
            and leaf in {"delitem", "delattr", "setitem", "setattr"}
        ):
            self.add(node, f"runtime monkeypatch via {receiver.id}.{leaf}", "MONKEYPATCH")
        if (
            leaf == "setattr"
            and node.args
            and (_literal_string(node.args[0]) or self._is_imported_root(node.args[0]))
        ):
            self.add(node, "runtime monkeypatch via setattr", "MONKEYPATCH")
        if leaf == "delattr" and node.args and self._is_imported_root(node.args[0]):
            self.add(node, "runtime monkeypatch via delattr", "MONKEYPATCH")

    def _check_dynamic_instance_call(self, node: ast.Call, qualified: str) -> None:
        if qualified not in {"delattr", "setattr", "builtins.delattr", "builtins.setattr"}:
            return
        if not node.args or not isinstance(node.args[0], ast.Name):
            return
        if node.args[0].id not in {"cls", "self"} or not self.class_stack:
            return
        if self.class_stack[-1].slots:
            return
        self.add(node, "dynamic instance attribute creation", "DYNAMIC-INSTANCE-ATTRIBUTE")

    def _check_blind_pass_through(self, node: ast.Call) -> None:
        if not self.pass_through_stack:
            return
        vararg, kwarg = self.pass_through_stack[-1]
        if vararg is not None:
            forwards_args = any(
                isinstance(argument, ast.Starred)
                and isinstance(argument.value, ast.Name)
                and argument.value.id == vararg
                for argument in node.args
            )
            if forwards_args:
                self.add(node, "*args blind pass-through", "VARIADIC-PASSTHROUGH", "ANN002")
        if kwarg is not None:
            forwards_kwargs = any(
                keyword.arg is None
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == kwarg
                for keyword in node.keywords
            )
            if forwards_kwargs:
                self.add(
                    node,
                    "**kwargs blind pass-through",
                    "VARIADIC-PASSTHROUGH",
                    "ANN003",
                )

    def _print_is_banned(self, qualified: str) -> bool:
        if not _is_production(self.unit.label) or qualified not in {"builtins.print", "print"}:
            return False
        path = Path(self.unit.label)
        parts = path.parts
        if "ralph" in parts:
            root = parts.index("ralph")
            if len(parts) > root + 1 and parts[root + 1] in {"cli", "contrib"}:
                return False
        name = path.name
        return not (name.startswith("bench_") or "_bench_" in name)

    def _is_sys_path_mutation(self, node: ast.Call) -> bool:
        if not isinstance(node.func, ast.Attribute):
            return False
        return (
            node.func.attr in _SYS_PATH_MUTATORS and self._qualified(node.func.value) == "sys.path"
        )

    def _is_all_mutation(self, node: ast.Call) -> bool:
        if not isinstance(node.func, ast.Attribute):
            return False
        return node.func.attr in _ALL_MUTATORS and self._is_all_expression(node.func.value)

    def _is_dunder_dict_mutation(self, node: ast.Call) -> bool:
        if not isinstance(node.func, ast.Attribute):
            return False
        if node.func.attr not in _DICT_MUTATORS:
            return False
        return self._is_dunder_dict_expression(node.func.value)

    def _check_thread_target(self, node: ast.Call, qualified: str) -> None:
        target: ast.expr | None = None
        if qualified in {"threading.Thread", "threading.start_new_thread"}:
            if qualified == "threading.Thread":
                target = next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "target"),
                    None,
                )
            elif node.args:
                target = node.args[0]
        if target is None:
            return
        function: ast.AST | None = None
        if isinstance(target, ast.Name):
            function = self.module_functions.get(target.id)
        elif isinstance(target, ast.Lambda):
            function = target
        for mutable, line in self.module_mutables.items():
            if function is not None and _loads_name(function, mutable):
                self.findings.append(
                    Finding(
                        self.unit.label,
                        line,
                        f"thread target closes over mutable {mutable}",
                        "THREAD-STATE",
                        "THREAD-STATE",
                    )
                )

    def visit_If(self, node: ast.If) -> None:
        parent = self.parents.get(node)
        if not (isinstance(parent, ast.If) and node in parent.orelse):
            count = 0
            current: ast.If | None = node
            while current is not None:
                if self._contains_isinstance(current.test):
                    count += 1
                next_if = current.orelse[0] if len(current.orelse) == 1 else None
                current = next_if if isinstance(next_if, ast.If) else None
            if count >= _DISPATCH_CHAIN_LENGTH:
                self.add(node, "isinstance if/elif dispatch chain", "TYPE-DISPATCH")
        self.generic_visit(node)

    def _contains_isinstance(self, node: ast.AST) -> bool:
        return any(
            isinstance(child, ast.Call)
            and self._qualified(child.func) in {"builtins.isinstance", "isinstance"}
            for child in ast.walk(node)
        )

    def visit_Compare(self, node: ast.Compare) -> None:
        expressions: list[ast.expr] = [node.left, *node.comparators]
        has_type_call = any(
            isinstance(expression, ast.Call)
            and self._qualified(expression.func) in {"builtins.type", "type"}
            and len(expression.args) == 1
            for expression in expressions
        )
        if has_type_call and any(
            operator.__class__ in {ast.Eq, ast.In, ast.Is, ast.IsNot, ast.NotEq, ast.NotIn}
            for operator in node.ops
        ):
            self.add(node, "type comparison dispatch", "TYPE-DISPATCH")
        self.generic_visit(node)

    def run(self) -> list[Finding]:
        self.visit(self.unit.tree)
        for node in ast.walk(self.unit.tree):
            if not isinstance(node, ast.Call):
                continue
            if self._qualified(node.func) not in {"builtins.isinstance", "isinstance"}:
                continue
            if len(node.args) >= _ATTRIBUTE_ARGUMENT_COUNT and isinstance(node.args[1], ast.Tuple):
                self.add(node, "tuple-form isinstance dispatch", "TYPE-DISPATCH")
        return self.findings


def _comment_findings(source: str, label: str) -> list[Finding]:
    findings: list[Finding] = []
    reader = io.StringIO(source).readline
    try:
        tokens = tokenize.generate_tokens(reader)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            if _TYPE_IGNORE_RE.search(token.string) is not None:
                findings.append(
                    Finding(label, token.start[0], "# type: ignore", "TYPE-IGNORE", "PGH003")
                )
            if _NOQA_RE.search(token.string) is not None:
                findings.append(Finding(label, token.start[0], "# noqa", "NOQA", "PGH004"))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return findings
    return findings


def _parse_unit(path: Path, label: str) -> _SourceUnit:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise _AuditInputError(f"cannot read {path}: {error}") from error
    try:
        tree = ast.parse(source, filename=label)
    except SyntaxError as error:
        line = error.lineno if error.lineno is not None else 1
        message = error.msg
        raise _AuditInputError(f"{label}:{line}: cannot parse Python source: {message}") from error
    return _SourceUnit(
        path,
        label,
        _module_from_label(label),
        _package_from_label(label),
        source,
        tree,
    )


def _source_units(paths: Sequence[Path], label_root: Path) -> list[_SourceUnit]:
    return [_parse_unit(path, _label(path, label_root)) for path in paths]
