"""Import-order audit: every ``ralph`` module must be importable first.

Python resolves ``from pkg.mod import name`` by reading ``name`` off the
module object. When an import cycle re-enters ``pkg.mod`` while it is still
executing, the module exists but is only partially initialised, and the
lookup fails with ``ImportError: cannot import name 'name' from partially
initialized module``. Whether that happens depends on WHICH module of the
cycle is imported first, so a cycle can pass every test -- whose import order
``conftest.py`` and earlier tests fix -- and still break the one entry point
that reaches it from the other side.

That is how ``make install`` broke: ``ralph.install`` reaches
``ralph.display`` first, and ``ralph.recovery.controller`` had started
importing the conflict-resolution package, whose status surface imports
``ralph.display.parallel_display`` back. Every test imported the pipeline
first and passed. A fresh interpreter importing each of the 1,425 modules on
its own showed 375 of them failing the same way.

The audit replays CPython's import machinery statically, once per module as
the first import of a fresh interpreter, over the module-level statements of
every file under ``ralph/``:

  * importing ``a.b.c`` imports ``a`` and ``a.b`` first, each to completion
    unless it is already executing;
  * a submodule becomes an attribute of its parent only once the submodule
    has finished executing;
  * ``from pkg import sub`` succeeds for a submodule that is still executing,
    because the import system falls back to ``sys.modules``;
  * a module that already defines a PEP 562 ``__getattr__`` resolves any name;
  * ``if TYPE_CHECKING:`` and ``if __name__ == "__main__":`` bodies never run
    (their ``else:`` arms do; ``if not TYPE_CHECKING:`` runs only its body),
    and imports inside functions run later, so none of them take part;
  * class bodies run at import time, so their imports take part;
  * an import inside a ``try`` whose handler catches ``ImportError`` cannot
    fail the module, because the handler runs instead.

Other branches (``if``/``try``/``with``/loops) are treated as executing
every arm. Modules outside ``ralph`` are opaque: they never import ``ralph``
back, so they cannot take part in a ``ralph`` cycle.

Usage:
    python -m ralph.testing.audit_import_order

Exit codes:
  0 = clean.
  1 = violations found, or a module could not be parsed.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.testing._audit_parse_error import AuditParseError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = [
    "PACKAGE",
    "AuditParseError",
    "Violation",
    "audit_modules",
    "audit_sources",
    "audit_tree",
    "collect_ops",
    "main",
]

#: The audited top-level package.
PACKAGE = "ralph"


@dataclass(frozen=True)
class Violation:
    """One module-level lookup of ``name`` on ``target`` while ``target`` is partially initialised.

    ``access`` is ``"from-import"`` for ``from target import name`` (an
    ``ImportError``) or ``"attribute"`` for a module-level ``target.name`` read
    through a bound module (an ``AttributeError``).
    """

    entry: str
    importer: str
    lineno: int
    target: str
    name: str
    chain: tuple[str, ...]
    access: str = "from-import"

    def __str__(self) -> str:
        statement = (
            f"`from {self.target} import {self.name}`"
            if self.access == "from-import"
            else f"`{self.target}.{self.name}` (read at module level)"
        )
        return (
            f"importing {self.entry} first fails: {self.importer}:{self.lineno} "
            f"{statement} runs while {self.target} is partially initialised\n"
            f"      chain: {' -> '.join(self.chain)}"
        )


@dataclass(frozen=True)
class _Import:
    module: str
    local: str
    alias: str


@dataclass(frozen=True)
class _FromImport:
    module: str
    names: tuple[tuple[str, str], ...]
    lineno: int
    guarded: bool


@dataclass(frozen=True)
class _Bind:
    name: str


@dataclass(frozen=True)
class _AttrRead:
    local: str
    attrs: tuple[str, ...]
    lineno: int
    guarded: bool


_Op = _Import | _FromImport | _Bind | _AttrRead


def _is_type_checking_guard(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _is_negated_type_checking_guard(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and _is_type_checking_guard(test.operand)
    )


#: Handler types that catch the ``ImportError`` a partially initialised module raises.
_IMPORT_ERROR_CATCHERS: frozenset[str] = frozenset({"ImportError", "Exception", "BaseException"})

#: Handler types that catch the ``AttributeError`` a partially initialised module raises.
_ATTRIBUTE_ERROR_CATCHERS: frozenset[str] = frozenset(
    {"AttributeError", "Exception", "BaseException"}
)


def _catches(handler_type: ast.expr | None, catchers: frozenset[str]) -> bool:
    if handler_type is None:
        return True
    if isinstance(handler_type, ast.Tuple):
        return any(_catches(element, catchers) for element in handler_type.elts)
    if isinstance(handler_type, ast.Name):
        return handler_type.id in catchers
    return isinstance(handler_type, ast.Attribute) and handler_type.attr in catchers


def _evaluated_expressions(node: ast.stmt) -> list[ast.expr]:
    """Return the expressions a module-level statement evaluates when it runs.

    Function bodies, lambda bodies and annotations are not evaluated at import
    time and are not returned; assignment targets are written, not read.
    """
    evaluated: list[ast.expr] = []
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.Expr)) or (
        isinstance(node, ast.AnnAssign) and node.value is not None
    ):
        evaluated = [node.value] if node.value is not None else []
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        defaults = [*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)]
        evaluated = [*node.decorator_list, *defaults]
    elif isinstance(node, ast.ClassDef):
        evaluated = [*node.decorator_list, *node.bases, *(k.value for k in node.keywords)]
    elif isinstance(node, (ast.If, ast.While)):
        evaluated = [node.test]
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        evaluated = [node.iter]
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        evaluated = [item.context_expr for item in node.items]
    elif isinstance(node, ast.Match):
        evaluated = [node.subject]
    return evaluated


def _attribute_chains(node: ast.AST) -> list[tuple[str, tuple[str, ...], int]]:
    """Return every ``name.attr[.attr...]`` read in ``node`` as ``(name, attrs, lineno)``."""
    if isinstance(node, ast.Lambda):
        return []
    if isinstance(node, ast.Attribute):
        attrs: list[str] = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            attrs.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            return [(current.id, tuple(reversed(attrs)), node.lineno)]
        return _attribute_chains(current)
    return [chain for child in ast.iter_child_nodes(node) for chain in _attribute_chains(child)]


def _is_main_guard(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for element in target.elts for name in _target_names(element)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


def _resolve_relative(module: str, is_package: bool, level: int, name: str | None) -> str:
    package_parts = module.split(".") if is_package else module.split(".")[:-1]
    base = package_parts[: len(package_parts) - (level - 1)]
    return ".".join([*base, name] if name else base)


@dataclass
class _OpCollector:
    module: str
    is_package: bool
    ops: list[_Op] = field(default_factory=list)
    guarded: bool = False
    attribute_guarded: bool = False

    def statements(self, body: Iterable[ast.stmt]) -> None:
        for statement in body:
            self.statement(statement)

    def statement(self, node: ast.stmt) -> None:
        if not isinstance(node, (ast.Try, ast.TryStar)):
            self.reads(node)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    self.ops.append(_Import(alias.name, alias.asname, alias.name))
                else:
                    root = alias.name.split(".")[0]
                    self.ops.append(_Import(alias.name, root, root))
        elif isinstance(node, ast.ImportFrom):
            self.from_import(node)
        elif isinstance(node, ast.If):
            self.conditional(node)
        elif isinstance(node, (ast.Try, ast.TryStar)):
            self.try_block(node)
        elif isinstance(node, ast.ClassDef):
            self.class_body(node)
        else:
            self.binds(node)
            self.nested(node)

    def reads(self, node: ast.stmt) -> None:
        """Record the ``module.attr`` reads a statement performs when it runs."""
        for expression in _evaluated_expressions(node):
            self.ops.extend(
                _AttrRead(local, attrs, lineno, self.attribute_guarded)
                for local, attrs, lineno in _attribute_chains(expression)
            )

    def binds(self, node: ast.stmt) -> None:
        """Record the names a non-import statement binds at module level."""
        targets: list[ast.expr] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.ops.append(_Bind(node.name))
        elif isinstance(node, ast.TypeAlias):
            targets.append(node.name)
        elif isinstance(node, ast.Assign):
            targets.extend(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor)) or (
            isinstance(node, ast.AnnAssign) and node.value is not None
        ):
            targets.append(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            targets.extend(item.optional_vars for item in node.items if item.optional_vars)
        self.ops.extend(_Bind(name) for target in targets for name in _target_names(target))

    def nested(self, node: ast.stmt) -> None:
        """Run the bodies of loops, ``with`` blocks and ``match`` cases."""
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            self.statements(node.body)
            self.statements(node.orelse)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            self.statements(node.body)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                self.statements(case.body)

    def class_body(self, node: ast.ClassDef) -> None:
        """Run a class body: its imports execute, but its names bind on the class."""
        body = _OpCollector(
            self.module,
            self.is_package,
            guarded=self.guarded,
            attribute_guarded=self.attribute_guarded,
        )
        body.statements(node.body)
        self.ops.extend(op for op in body.ops if not isinstance(op, _Bind))
        self.ops.append(_Bind(node.name))

    def conditional(self, node: ast.If) -> None:
        if _is_type_checking_guard(node.test) or _is_main_guard(node.test):
            self.statements(node.orelse)
        elif _is_negated_type_checking_guard(node.test):
            self.statements(node.body)
        else:
            self.statements(node.body)
            self.statements(node.orelse)

    def try_block(self, node: ast.Try | ast.TryStar) -> None:
        # A failing import inside a ``try`` whose handler catches ImportError
        # does not fail the module: the handler runs instead.
        outer = (self.guarded, self.attribute_guarded)
        self.guarded = outer[0] or any(
            _catches(h.type, _IMPORT_ERROR_CATCHERS) for h in node.handlers
        )
        self.attribute_guarded = outer[1] or any(
            _catches(h.type, _ATTRIBUTE_ERROR_CATCHERS) for h in node.handlers
        )
        self.statements(node.body)
        self.guarded, self.attribute_guarded = outer
        for handler in node.handlers:
            if handler.name:
                self.ops.append(_Bind(handler.name))
            self.statements(handler.body)
        self.statements(node.orelse)
        self.statements(node.finalbody)

    def from_import(self, node: ast.ImportFrom) -> None:
        if node.level:
            target = _resolve_relative(self.module, self.is_package, node.level, node.module)
        else:
            target = node.module or ""
        if target == "__future__":
            return
        names = tuple((alias.name, alias.asname or alias.name) for alias in node.names)
        self.ops.append(_FromImport(target, names, node.lineno, self.guarded))


def collect_ops(source: str, module: str, *, is_package: bool, label: str) -> tuple[_Op, ...]:
    """Return the module-level import and binding operations of one module, in order.

    Raises:
        AuditParseError: when ``source`` is not valid Python.
    """
    try:
        tree = ast.parse(source, filename=label)
    except SyntaxError as exc:
        raise AuditParseError(label, exc) from exc
    collector = _OpCollector(module, is_package)
    collector.statements(tree.body)
    return tuple(collector.ops)


@dataclass
class _Simulation:
    modules: Mapping[str, tuple[_Op, ...]]
    entry: str
    bound: dict[str, set[str]] = field(default_factory=dict)
    aliases: dict[str, dict[str, str]] = field(default_factory=dict)
    executing: list[str] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)

    def import_module(self, name: str) -> None:
        if name in self.bound or name not in self.modules:
            return
        parent, _, child = name.rpartition(".")
        if parent:
            self.import_module(parent)
            if name in self.bound:
                return
        self.bound[name] = set()
        self.aliases[name] = {}
        self.executing.append(name)
        for op in self.modules[name]:
            self.run(name, op)
        self.executing.pop()
        if parent in self.bound:
            self.bound[parent].add(child)

    def bind(self, module: str, name: str, alias: str | None = None) -> None:
        self.bound[module].add(name)
        if alias is None:
            self.aliases[module].pop(name, None)
        else:
            self.aliases[module][name] = alias

    def run(self, module: str, op: _Op) -> None:
        if isinstance(op, _Bind):
            self.bind(module, op.name)
        elif isinstance(op, _Import):
            parts = op.module.split(".")
            for depth in range(1, len(parts) + 1):
                self.import_module(".".join(parts[:depth]))
            self.bind(module, op.local, op.alias)
        elif isinstance(op, _AttrRead):
            self.read(module, op)
        else:
            self.from_import(module, op)

    def from_import(self, module: str, op: _FromImport) -> None:
        self.import_module(op.module)
        target_names = self.bound.get(op.module)
        for name, bound in op.names:
            submodule = f"{op.module}.{name}"
            if name == "*":
                exported = target_names or set()
                for star in [n for n in exported if not n.startswith("_")]:
                    self.bind(module, star)
                continue
            if submodule in self.modules:
                if name not in self.bound[op.module]:
                    self.import_module(submodule)
                self.bind(module, bound, submodule)
                continue
            self.bind(module, bound)
            if target_names is None or op.guarded or op.module not in self.executing:
                continue
            if name not in target_names and "__getattr__" not in target_names:
                self.report(module, op.lineno, op.module, name, "from-import")

    def read(self, module: str, op: _AttrRead) -> None:
        target = self.aliases[module].get(op.local)
        for attr in op.attrs:
            names = self.bound.get(target) if target is not None else None
            if target is None or names is None:
                return
            submodule = f"{target}.{attr}"
            if attr not in names:
                # A submodule only becomes its parent's attribute once it has
                # finished executing, so reading a still-executing one fails too.
                partial = target in self.executing or submodule in self.executing
                if partial and "__getattr__" not in names and not op.guarded:
                    self.report(module, op.lineno, target, attr, "attribute")
                return
            target = submodule if submodule in self.modules else None

    def report(self, module: str, lineno: int, target: str, name: str, access: str) -> None:
        self.violations.append(
            Violation(
                entry=self.entry,
                importer=module,
                lineno=lineno,
                target=target,
                name=name,
                chain=tuple(self.executing),
                access=access,
            )
        )


def audit_modules(modules: Mapping[str, tuple[_Op, ...]]) -> list[Violation]:
    """Return the first-import violations of every module in ``modules``.

    ``modules`` maps each dotted module name to its :func:`collect_ops`
    result; every package on a module's path must be present (an empty op
    tuple stands for a namespace package). Each distinct failing statement is
    reported once, with the first entry point (in sorted order) that reaches it.
    """
    seen: set[tuple[str, int, str, str]] = set()
    violations: list[Violation] = []
    for entry in sorted(modules):
        simulation = _Simulation(modules, entry)
        simulation.import_module(entry)
        for violation in simulation.violations:
            key = (violation.importer, violation.lineno, violation.target, violation.name)
            if key not in seen:
                seen.add(key)
                violations.append(violation)
    return violations


def audit_sources(sources: Mapping[str, str]) -> list[Violation]:
    """Audit in-memory sources keyed by repository-relative path (``ralph/x.py``)."""
    modules: dict[str, tuple[_Op, ...]] = {}
    for label, source in sources.items():
        parts = Path(label).with_suffix("").parts
        is_package = parts[-1] == "__init__"
        dotted = ".".join(parts[:-1] if is_package else parts)
        for depth in range(1, dotted.count(".") + 1):
            modules.setdefault(".".join(dotted.split(".")[:depth]), ())
        modules[dotted] = collect_ops(source, dotted, is_package=is_package, label=label)
    return audit_modules(modules)


def audit_tree(package_root: Path) -> tuple[list[Violation], int]:
    """Return the violations under ``package_root / ralph`` and the module count.

    Raises:
        FileNotFoundError: when ``package_root / ralph`` is not a directory.
            Silently skipping it would report a vacuous clean run.
    """
    base = package_root / PACKAGE
    if not base.is_dir():
        raise FileNotFoundError(f"audit root is not a directory: {base}")
    sources = {
        str(path.relative_to(package_root)): path.read_text(encoding="utf-8")
        for path in sorted(base.rglob("*.py"))
    }
    return audit_sources(sources), len(sources)


def main(argv: list[str] | None = None) -> int:
    """Run the audit over ``ralph/`` in the current directory and report violations."""
    del argv
    try:
        violations, scanned = audit_tree(Path.cwd())
    except FileNotFoundError as exc:
        print(exc)
        return 1
    except AuditParseError as exc:
        print(f"Could not parse a file under audit: {exc}")
        return 1
    if not violations:
        print(f"All {scanned} module(s) under {PACKAGE}/ import cleanly when imported first.")
        return 0
    print(f"IMPORT-ORDER VIOLATIONS: {len(violations)} in {scanned} module(s)")
    print("=" * 72)
    for violation in violations:
        print(f"  {violation}")
    print(
        "\nBreak the cycle at the edge that points the wrong way between layers, or "
        "bind the module (`from pkg import mod`) and read the name at call time."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
