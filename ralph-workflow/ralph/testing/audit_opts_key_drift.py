"""Silently-dropped ``**opts`` key audit.

The complement to :mod:`ralph.testing.audit_kwargs_forwarding`. An untyped
``**opts`` seam has two failure modes, and they are opposites:

  * a key passed twice -- once explicitly, once through a forward -- raises
    ``TypeError: got multiple values for keyword argument`` at call time. That
    is the loud half, and ``audit_kwargs_forwarding`` bans it.

  * a key passed under a name the callee never reads is silently discarded.
    Nothing raises, nothing logs; the feature the key carried just never
    happens. That is the quiet half, and this audit bans it.

``ralph/pipeline/runner.py::_run_fan_out_phase`` passed ``monitor_stop_cb``
into ``execute_fan_out_sync``, whose callee reads ``_monitor_stop_cb`` out of
``opts``. The callback vanished, so the fan-out's ``SignalBridge`` never had
``_connectivity_stop`` wired and interrupting a parallel phase left the
connectivity probe running. Nothing failed, so nothing caught it: the only
test that drove the seam stubbed it with ``def _fan_out(**kwargs)``, which
absorbs any spelling at all.

How a call is judged
--------------------
For every call whose target resolves to a ``ralph`` function declaring a
catch-all, the audit computes the set of names that call can actually reach:

  * the callee's own named parameters, and
  * the literal string keys read out of the catch-all -- ``opts["k"]``,
    ``opts.get("k")``, ``opts.pop("k")``, ``"k" in opts`` -- followed
    transitively through ``**`` re-forwards and through the dict being handed
    to a helper as an ordinary argument.

A keyword the caller passes that is in neither set is dead on arrival.

Fail-quiet on opacity, not fail-loud
------------------------------------
When a callee does anything the audit cannot follow -- a non-literal key, a
``update``/``keys``/``items`` call, a rebind of the dict, a forward into a
target that cannot be resolved -- that callee is marked dynamic and every call
to it is skipped rather than reported. A gate that cries wolf gets deleted, so
this one only speaks when it can prove the key is unreachable.

Resolution is pure AST across the parsed tree: ``from x import f``,
``from x import f as g`` and ``import x.y as m`` / ``m.f`` are followed by
reading each module's own import statements. Nothing is imported, executed, or
read outside the source files themselves.

Usage:
    python -m ralph.testing.audit_opts_key_drift [root1 root2 ...]

Exit codes:
  0 = clean.
  1 = violations found.
  2 = a named root does not exist.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ROOTS: tuple[str, ...] = ("ralph",)

#: Catch-all reads whose key is a literal string are followed; any other use of
#: the dict marks the callee dynamic.
_LITERAL_READS = frozenset({"get", "pop", "setdefault"})
#: Whole-dict operations that make the consumed key set unknowable.
_OPAQUE_READS = frozenset({"update", "keys", "items", "values"})

_FuncNode = ast.FunctionDef | ast.AsyncFunctionDef
_FUNC_NODES: tuple[type[ast.AST], ...] = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Violation:
    """One keyword that cannot reach anything in the callee."""

    path: str
    lineno: int
    callee: str
    keyword: str

    def __str__(self) -> str:
        return (
            f"{self.path}:{self.lineno} passes {self.keyword}=... to {self.callee}(), "
            f"which never reads '{self.keyword}' from its catch-all and has no such "
            f"parameter; the value is silently discarded."
        )


@dataclass
class _Module:
    """One parsed module: its functions and the names its imports bind."""

    name: str
    tree: ast.Module
    functions: dict[str, _FuncNode] = field(default_factory=dict)
    #: local name -> (module, attribute) for ``from x import f [as g]``
    from_imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: local name -> module for ``import x.y as m`` / ``import x``
    module_aliases: dict[str, str] = field(default_factory=dict)


def _module_name(path: Path, package_root: Path) -> str:
    parts = list(path.relative_to(package_root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _index_module(name: str, tree: ast.Module) -> _Module:
    module = _Module(name=name, tree=tree)
    for node in ast.walk(tree):
        if isinstance(node, _FUNC_NODES):
            func = node
            assert isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef)
            module.functions.setdefault(func.name, func)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or node.level:
                continue
            for alias in node.names:
                module.from_imports[alias.asname or alias.name] = (node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module.module_aliases[alias.asname or alias.name.split(".")[0]] = alias.name
    return module


def _lookup(
    index: dict[str, _Module],
    module_name: str | None,
    attribute: str,
) -> tuple[_Module, _FuncNode] | None:
    """Return the named function in the named module, when both are indexed."""
    target = index.get(module_name) if module_name is not None else None
    if target is None:
        return None
    node = target.functions.get(attribute)
    return (target, node) if node is not None else None


def _resolve_name(
    module: _Module,
    name: str,
    index: dict[str, _Module],
) -> tuple[_Module, _FuncNode] | None:
    """Resolve a bare name to a local def or a ``from x import f`` target."""
    local = module.functions.get(name)
    if local is not None:
        return module, local
    imported = module.from_imports.get(name)
    if imported is None:
        return None
    return _lookup(index, imported[0], imported[1])


def _resolve_attribute(
    module: _Module,
    value: ast.Name,
    attribute: str,
    index: dict[str, _Module],
) -> tuple[_Module, _FuncNode] | None:
    """Resolve ``m.f`` where ``m`` is an imported module or module alias."""
    module_name = module.module_aliases.get(value.id)
    if module_name is None:
        imported = module.from_imports.get(value.id)
        module_name = f"{imported[0]}.{imported[1]}" if imported else None
    return _lookup(index, module_name, attribute)


def _resolve_call_target(
    module: _Module,
    func: ast.expr,
    index: dict[str, _Module],
) -> tuple[_Module, _FuncNode] | None:
    """Resolve a call's target to a (module, function) pair, or None."""
    if isinstance(func, ast.Name):
        return _resolve_name(module, func.id, index)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return _resolve_attribute(module, func.value, func.attr, index)
    return None


def _named_parameters(fn: _FuncNode) -> set[str]:
    args = fn.args
    return {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}


def _direct_reads(fn: _FuncNode, variable: str) -> tuple[set[str], bool]:
    """Return literal keys read from ``variable``, and whether any read is opaque."""
    keys: set[str] = set()
    dynamic = False
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == variable
        ):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                keys.add(node.slice.value)
            else:
                dynamic = True
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == variable
        ):
            attr = node.func.attr
            if (
                attr in _LITERAL_READS
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                keys.add(node.args[0].value)
            elif attr in _LITERAL_READS or attr in _OPAQUE_READS:
                dynamic = True
        elif isinstance(node, ast.Compare):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Name) and comparator.id == variable:
                    if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
                        keys.add(node.left.value)
                    else:
                        dynamic = True
        elif (
            isinstance(node, ast.Name)
            and node.id == variable
            and isinstance(node.ctx, ast.Store)
        ):
            dynamic = True
    return keys, dynamic


def _reachable_names(
    module: _Module,
    fn: _FuncNode,
    variable: str,
    index: dict[str, _Module],
    seen: set[tuple[str, str, str]],
) -> tuple[set[str], bool]:
    """Return every name ``variable`` can reach in ``fn``, transitively.

    The second element is True when the analysis hit something it could not
    follow, in which case callers must not report anything.
    """
    marker = (module.name, fn.name, variable)
    if marker in seen:
        return set(), False
    seen.add(marker)

    keys, dynamic = _direct_reads(fn, variable)
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        resolved = _resolve_call_target(module, node.func, index)

        for keyword in node.keywords:
            if keyword.arg is not None:
                continue
            if not (isinstance(keyword.value, ast.Name) and keyword.value.id == variable):
                continue
            if resolved is None:
                dynamic = True
                continue
            callee_module, callee = resolved
            keys |= _named_parameters(callee)
            if callee.args.kwarg is not None:
                sub_keys, sub_dynamic = _reachable_names(
                    callee_module, callee, callee.args.kwarg.arg, index, seen
                )
                keys |= sub_keys
                dynamic = dynamic or sub_dynamic

        handed_off = [*node.args, *(kw.value for kw in node.keywords if kw.arg is not None)]
        for position, argument in enumerate(handed_off):
            if not (isinstance(argument, ast.Name) and argument.id == variable):
                continue
            if resolved is None:
                dynamic = True
                continue
            callee_module, callee = resolved
            positional = [
                arg.arg for arg in (*callee.args.posonlyargs, *callee.args.args)
            ]
            if position >= len(positional):
                dynamic = True
                continue
            sub_keys, sub_dynamic = _reachable_names(
                callee_module, callee, positional[position], index, seen
            )
            keys |= sub_keys
            dynamic = dynamic or sub_dynamic
    return keys, dynamic


def audit_index(index: dict[str, _Module]) -> list[Violation]:
    """Return every keyword that cannot reach anything in its resolved callee."""
    reachable: dict[tuple[str, str], tuple[set[str], bool] | None] = {}

    def _for(callee_module: _Module, callee: _FuncNode) -> tuple[set[str], bool] | None:
        marker = (callee_module.name, callee.name)
        if marker not in reachable:
            if callee.args.kwarg is None:
                reachable[marker] = None
            else:
                reachable[marker] = _reachable_names(
                    callee_module, callee, callee.args.kwarg.arg, index, set()
                )
        return reachable[marker]

    violations: list[Violation] = []
    for module in index.values():
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Call) or not node.keywords:
                continue
            resolved = _resolve_call_target(module, node.func, index)
            if resolved is None:
                continue
            callee_module, callee = resolved
            names = _for(callee_module, callee)
            if names is None:
                continue
            keys, dynamic = names
            if dynamic:
                continue
            allowed = keys | _named_parameters(callee)
            for keyword in node.keywords:
                if keyword.arg is None or keyword.arg in allowed:
                    continue
                violations.append(
                    Violation(
                        path=module.name.replace(".", "/") + ".py",
                        lineno=node.lineno,
                        callee=f"{callee_module.name}.{callee.name}",
                        keyword=keyword.arg,
                    )
                )
    return violations


def build_index(package_root: Path, roots: tuple[str, ...] = DEFAULT_ROOTS) -> dict[str, _Module]:
    """Parse and index every module under ``roots``.

    Raises:
        FileNotFoundError: when a named root is not a directory. Silently
            skipping it would report a vacuous clean run.
    """
    index: dict[str, _Module] = {}
    for root in roots:
        base = package_root / root
        if not base.is_dir():
            raise FileNotFoundError(f"audit root is not a directory: {base}")
        for path in sorted(base.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            name = _module_name(path, package_root)
            index[name] = _index_module(name, tree)
    return index


def audit_tree(
    package_root: Path,
    roots: tuple[str, ...] = DEFAULT_ROOTS,
) -> tuple[list[Violation], int]:
    """Return violations under ``package_root`` and the number of modules indexed."""
    index = build_index(package_root, roots)
    return audit_index(index), len(index)


def main(argv: list[str] | None = None) -> int:
    """Run the audit over the repository and report violations."""
    args = list(sys.argv[1:] if argv is None else argv)
    package_root = Path.cwd()
    roots = tuple(args) if args else DEFAULT_ROOTS
    try:
        violations, scanned = audit_tree(package_root, roots)
    except FileNotFoundError as exc:
        print(exc)
        return 2
    if not violations:
        print(f"No silently-dropped **opts keys in {scanned} module(s) under {', '.join(roots)}.")
        return 0
    print(f"SILENTLY-DROPPED **opts KEYS: {len(violations)} in {scanned} module(s)")
    print("=" * 72)
    for violation in violations:
        print(f"  {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
