"""Single-pass AST analysis of Ralph test modules.

The test-policy audit needs, for every parsed test module:

* the three white-box coupling counts (private monkeypatch targets, private
  ``ralph`` imports, and private attribute reads in high-confidence contexts),
* the exact dotted targets the module patches,
* the pytest markers the module declares, and
* every call site with its dotted callee name and its wait-for context.

All of it comes from ONE traversal per module
(:func:`analyze_module`). ``ast.parse`` is the irreducible cost of auditing
the whole tree; every additional tree walk is not. The detectors below used
to walk each module about ten times (once per detector, plus a fixpoint loop
over the assignments and a re-walk of every ``assert`` subtree), which made
the whole-tree audit cost more than its own per-step wall-clock cap.

The ONE traversal collects the nodes; the three CONTEXTS that need ancestry
(an attribute inside an ``assert``, an attribute inside a
``monkeypatch.setattr`` target, a call inside an ``asyncio.wait_for``) are
resolved afterwards against the collected nodes, because resolving them
during the walk would put a context tuple on every stack entry.

The public helpers (:func:`count_setattr_private`, :func:`count_private_imports`,
:func:`count_private_attrs`, :func:`collect_whitebox_counts` and
:func:`collect_patched_targets`) are thin wrappers over that single
traversal, so a caller that only needs one number still pays for one walk.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

WhiteboxCounts = dict[str, int]

#: One call site: the call node, its dotted callee name (``None`` when the
#: callee is not a plain dotted name), and whether the call sits inside an
#: ``asyncio.wait_for`` / ``asyncio.timeout`` expression.
CallSite = tuple[ast.Call, str | None, bool]

_DUNDER_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "__all__",
        "__name__",
        "__class__",
        "__type__",
        "__module__",
        "__dict__",
        "__init__",
        "__enter__",
        "__exit__",
        "__doc__",
    }
)

# The calls whose arguments are a blocking wait that the caller already
# bounded with a timeout.
_WAIT_FOR_NAMES: frozenset[str] = frozenset({"asyncio.wait_for", "asyncio.timeout"})

# The number of leading arguments of a ``monkeypatch.setattr`` call that
# name the patched target (the target itself and the patched attribute).
_SETATTR_TARGET_ARGUMENT_COUNT = 2

# The number of arguments a ``mock.patch.object`` call needs: the patched
# receiver and the patched attribute.
_PATCH_OBJECT_ARGUMENT_COUNT = 2


@dataclass
class _Scan:
    """The node buckets ONE traversal collects."""

    call_sites: list[tuple[ast.Call, str | None]] = field(default_factory=list)
    attributes: list[ast.Attribute] = field(default_factory=list)
    imports: list[ast.Import | ast.ImportFrom] = field(default_factory=list)
    assignments: list[tuple[tuple[ast.expr, ...], ast.expr | None]] = field(default_factory=list)
    fixture_arguments: list[tuple[str, ...]] = field(default_factory=list)
    decorators: list[ast.expr] = field(default_factory=list)
    pytestmark_values: list[ast.expr] = field(default_factory=list)
    asserts: list[ast.Assert] = field(default_factory=list)


@dataclass(frozen=True)
class ModuleFacts:
    """Every fact the test-policy audit needs from ONE parsed test module."""

    counts: WhiteboxCounts
    patched_targets: frozenset[str]
    monkeypatch_names: frozenset[str]
    call_sites: tuple[CallSite, ...]
    markers: frozenset[str]
    has_subprocess_e2e_marker: bool


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _is_monkeypatch_fixture_name(name: str) -> bool:
    return name == "monkeypatch" or name.startswith("monkeypatch")


def _is_ralph_module(module: str) -> bool:
    return module == "ralph" or module.startswith("ralph.")


def _module_last_segment(module: str) -> str:
    return module.rsplit(".", 1)[-1]


def _dotted_name(node: ast.expr) -> str | None:
    """Return the dotted name of ``node`` (``None`` when it has none)."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    parts.reverse()
    return ".".join(parts)


def _argument_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    """Return the positional and keyword-only argument names of ``node``."""
    arguments = node.args
    return tuple(
        argument.arg
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    )


def _is_monkeypatch_setattr(node: ast.Call, bound: Collection[str]) -> bool:
    """Return True when ``node`` patches through a monkeypatch fixture."""
    function = node.func
    if not isinstance(function, ast.Attribute) or function.attr != "setattr":
        return False
    receiver = function.value
    return isinstance(receiver, ast.Name) and receiver.id in bound


def _record_import(scan: _Scan, node: ast.AST) -> None:
    """Record an import node."""
    if isinstance(node, ast.Import | ast.ImportFrom):
        scan.imports.append(node)


def _record_assign(scan: _Scan, node: ast.AST) -> None:
    """Record an assignment and the ``pytestmark`` it may declare."""
    if isinstance(node, ast.Assign):
        targets = tuple(node.targets)
        scan.assignments.append((targets, node.value))
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "pytestmark":
                scan.pytestmark_values.append(node.value)
    elif isinstance(node, ast.AnnAssign):
        scan.assignments.append(((node.target,), node.value))


def _record_function(scan: _Scan, node: ast.AST) -> None:
    """Record a function's fixture arguments and its decorators.

    Only a SYNCHRONOUS function contributes decorators: the marker grammar
    of the audit reads ``@pytest.mark.*`` off a synchronous function or a
    class definition. An asynchronous function still contributes its fixture
    arguments, because the monkeypatch fixture can be requested there too.
    """
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        scan.fixture_arguments.append(_argument_names(node))
    if isinstance(node, ast.FunctionDef):
        scan.decorators.extend(node.decorator_list)


def _record_class(scan: _Scan, node: ast.AST) -> None:
    """Record a class's decorators."""
    if isinstance(node, ast.ClassDef):
        scan.decorators.extend(node.decorator_list)


def _record_assert(scan: _Scan, node: ast.AST) -> None:
    """Record an assert statement."""
    if isinstance(node, ast.Assert):
        scan.asserts.append(node)


#: The node types the ONE traversal records outside the call and attribute
#: buckets. A dispatch table keeps the hot loop free of a long isinstance
#: chain.
_SCAN_HANDLERS: dict[type, Callable[[_Scan, ast.AST], None]] = {
    ast.Import: _record_import,
    ast.ImportFrom: _record_import,
    ast.Assign: _record_assign,
    ast.AnnAssign: _record_assign,
    ast.FunctionDef: _record_function,
    ast.AsyncFunctionDef: _record_function,
    ast.ClassDef: _record_class,
    ast.Assert: _record_assert,
}


def _scan_tree(tree: ast.AST) -> _Scan:
    """Collect every node category the detectors need in ONE traversal.

    The children are pushed in reverse field order so the stack pops them
    back in source order, which keeps the collected call sites in the same
    order an ``ast.NodeVisitor`` would report them.
    """
    scan = _Scan()
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call):
            scan.call_sites.append((node, _dotted_name(node.func)))
        elif isinstance(node, ast.Attribute):
            scan.attributes.append(node)
        else:
            handler = _SCAN_HANDLERS.get(type(node))
            if handler is not None:
                handler(scan, node)
        for field_name in reversed(node._fields):
            value: object = getattr(node, field_name, None)
            if isinstance(value, list):
                items = [item for item in value if isinstance(item, ast.AST)]
                stack.extend(reversed(items))
            elif isinstance(value, ast.AST):
                stack.append(value)
    return scan


def _monkeypatch_names(scan: _Scan) -> set[str]:
    """Return every name bound to pytest's monkeypatch fixture."""
    bound = {"monkeypatch"}
    for names in scan.fixture_arguments:
        for name in names:
            if _is_monkeypatch_fixture_name(name):
                bound.add(name)

    changed = True
    while changed:
        changed = False
        for targets, value in scan.assignments:
            if not isinstance(value, ast.Name) or value.id not in bound:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in bound:
                    bound.add(target.id)
                    changed = True
    return bound


def _count_setattr_private(
    call_sites: list[tuple[ast.Call, str | None]], bound: Collection[str]
) -> int:
    """Count monkeypatch calls that target a private production attribute."""
    count = 0
    for node, _call_name in call_sites:
        if not _is_monkeypatch_setattr(node, bound):
            continue
        if not node.args:
            continue

        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            target = first.value.rsplit(".", 1)[-1]
            if target.startswith("_") and not _is_dunder(target):
                count += 1
                continue

        second_arguments = node.args[1:2]
        if not second_arguments:
            continue
        second = second_arguments[0]
        if (
            isinstance(second, ast.Constant)
            and isinstance(second.value, str)
            and second.value.startswith("_")
            and not _is_dunder(second.value)
        ):
            count += 1
    return count


def _count_private_imports(imports: list[ast.Import | ast.ImportFrom]) -> int:
    """Count imports that reach private ``ralph`` production internals."""
    count = 0
    for node in imports:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not _is_ralph_module(module):
                continue
            if _module_last_segment(module).startswith("_"):
                count += 1
            for alias in node.names:
                if alias.name.startswith("_") and alias.name != "*" and not _is_dunder(alias.name):
                    count += 1
        else:
            for alias in node.names:
                if not _is_ralph_module(alias.name):
                    continue
                if _module_last_segment(alias.name).startswith("_"):
                    count += 1
    return count


def _ralph_module_names(imports: list[ast.Import | ast.ImportFrom]) -> set[str]:
    """Return the names bound by imports that reach a ``ralph`` module."""
    names: set[str] = set()
    for node in imports:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_ralph_module(module):
                for alias in node.names:
                    names.add(alias.asname or alias.name)
        else:
            for alias in node.names:
                if _is_ralph_module(alias.name):
                    names.add(alias.asname or alias.name.split(".")[0])
    return names


def _assert_attribute_ids(asserts: list[ast.Assert]) -> set[int]:
    """Return the identities of the attributes that sit inside an ``assert``."""
    identities: set[int] = set()
    for node in asserts:
        for descendant in ast.walk(node):
            if isinstance(descendant, ast.Attribute):
                identities.add(id(descendant))
    return identities


def _setattr_target_attribute_ids(
    call_sites: list[tuple[ast.Call, str | None]],
    bound: Collection[str],
) -> set[int]:
    """Return the identities of the attributes a monkeypatch patches."""
    identities: set[int] = set()
    for node, _call_name in call_sites:
        if not _is_monkeypatch_setattr(node, bound):
            continue
        for argument in node.args[:_SETATTR_TARGET_ARGUMENT_COUNT]:
            for descendant in ast.walk(argument):
                if isinstance(descendant, ast.Attribute):
                    identities.add(id(descendant))
    return identities


def _count_private_attrs(scan: _Scan, bound: Collection[str]) -> int:
    """Count private attribute accesses in high-confidence test contexts."""
    ralph_modules = _ralph_module_names(scan.imports)
    assert_identities = _assert_attribute_ids(scan.asserts)
    setattr_identities = _setattr_target_attribute_ids(scan.call_sites, bound)

    count = 0
    for node in scan.attributes:
        attribute = node.attr
        if not attribute.startswith("_") or attribute in _DUNDER_ATTRIBUTES:
            continue
        if _is_dunder(attribute):
            continue
        identity = id(node)
        if identity in assert_identities or identity in setattr_identities:
            count += 1
            continue
        if isinstance(node.value, ast.Name) and node.value.id in ralph_modules:
            count += 1
    return count


def _whitebox_counts(scan: _Scan, bound: Collection[str]) -> WhiteboxCounts:
    """Return the three white-box detector counts of one scanned module."""
    return {
        "setattr_private": _count_setattr_private(scan.call_sites, bound),
        "private_import": _count_private_imports(scan.imports),
        "private_attr": _count_private_attrs(scan, bound),
    }


def canonical_patch_target(receiver: str, attribute: str) -> str:
    """Return the canonical dotted target for a patched attribute."""
    if receiver == "Path":
        receiver = "pathlib.Path"
    return f"{receiver}.{attribute}"


def canonical_target_string(target: str) -> str:
    """Return the canonical spelling of a string patch target."""
    if target == "open":
        return "builtins.open"
    if target.startswith("Path."):
        return f"pathlib.{target}"
    return target


def _add_setattr_targets(targets: set[str], node: ast.Call) -> None:
    """Record the targets a ``monkeypatch.setattr`` call patches."""
    if node.args and isinstance(node.args[0], ast.Constant):
        first_node = node.args[0]
        if isinstance(first_node.value, str):
            targets.add(canonical_target_string(first_node.value))
    attribute_arguments = node.args[1:2]
    if not attribute_arguments:
        return
    attribute_constant = attribute_arguments[0]
    if not isinstance(attribute_constant, ast.Constant):
        return
    if not isinstance(attribute_constant.value, str):
        return
    receiver = _dotted_name(node.args[0])
    if receiver is not None:
        targets.add(canonical_patch_target(receiver, attribute_constant.value))


def _patched_targets(
    scan: _Scan,
    bound: Collection[str],
    monkeypatch_patterns: Collection[str],
) -> set[str]:
    """Collect the exact dotted targets patched in one scanned module."""
    normalized = {pattern.removesuffix("(") for pattern in monkeypatch_patterns}
    targets: set[str] = set()
    for node, function_name in scan.call_sites:
        if function_name is None:
            continue

        if _is_monkeypatch_setattr(node, bound):
            _add_setattr_targets(targets, node)
            continue

        if function_name in normalized and node.args:
            patch_target_node = node.args[0]
            if isinstance(patch_target_node, ast.Constant) and isinstance(
                patch_target_node.value, str
            ):
                targets.add(canonical_target_string(patch_target_node.value))
            continue

        if (
            function_name.endswith(".patch.object")
            and len(node.args) >= _PATCH_OBJECT_ARGUMENT_COUNT
        ):
            receiver = _dotted_name(node.args[0])
            attribute_expression = node.args[1]
            if (
                receiver is not None
                and isinstance(attribute_expression, ast.Constant)
                and isinstance(attribute_expression.value, str)
            ):
                targets.add(canonical_patch_target(receiver, attribute_expression.value))
    return targets


def _is_subprocess_e2e_decorator(node: ast.AST) -> bool:
    """Return True when ``node`` is ``@pytest.mark.subprocess_e2e``."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "subprocess_e2e"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _harvest_markers_from_value(value: ast.AST, markers: set[str]) -> None:
    """Walk a single marker expression and record its name."""
    if isinstance(value, ast.Attribute):
        if (
            isinstance(value.value, ast.Attribute)
            and value.value.attr == "mark"
            and isinstance(value.value.value, ast.Name)
            and value.value.value.id == "pytest"
        ):
            markers.add(value.attr)
    elif isinstance(value, ast.Call):
        if isinstance(value.func, ast.Attribute):
            _harvest_markers_from_value(value.func, markers)
    elif isinstance(value, ast.List):
        for elt in value.elts:
            _harvest_markers_from_value(elt, markers)


def _collect_markers(scan: _Scan) -> set[str]:
    """Return every pytest marker name the module declares."""
    markers: set[str] = set()
    for value in scan.pytestmark_values:
        _harvest_markers_from_value(value, markers)
    for decorator in scan.decorators:
        _harvest_markers_from_value(decorator, markers)
    return markers


def _has_subprocess_e2e_marker(scan: _Scan) -> bool:
    """Return True when the module declares the ``subprocess_e2e`` marker."""
    for decorator in scan.decorators:
        if _is_subprocess_e2e_decorator(decorator):
            return True
    for value in scan.pytestmark_values:
        if _is_subprocess_e2e_decorator(value):
            return True
        if isinstance(value, ast.List):
            for elt in value.elts:
                if _is_subprocess_e2e_decorator(elt):
                    return True
    return False


def _wait_for_call_ids(call_sites: list[tuple[ast.Call, str | None]]) -> set[int]:
    """Return the identities of the calls nested inside a wait-for call."""
    identities: set[int] = set()
    for node, call_name in call_sites:
        if call_name not in _WAIT_FOR_NAMES:
            continue
        for descendant in ast.walk(node):
            if descendant is not node and isinstance(descendant, ast.Call):
                identities.add(id(descendant))
    return identities


def has_subprocess_e2e_marker(tree: ast.AST) -> bool:
    """Return True when ``tree`` declares the ``subprocess_e2e`` marker.

    This is the cheap, marker-only probe the whole-tree exemption scan uses;
    it walks the module once but collects nothing else.
    """
    decorators: list[ast.expr] = []
    pytestmark_values: list[ast.expr] = []
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            decorators.extend(node.decorator_list)
        elif isinstance(node, ast.Assign):
            pytestmark_values.extend(
                node.value
                for target in node.targets
                if isinstance(target, ast.Name) and target.id == "pytestmark"
            )
        stack.extend(ast.iter_child_nodes(node))
    marker_scan = _Scan(decorators=decorators, pytestmark_values=pytestmark_values)
    return _has_subprocess_e2e_marker(marker_scan)


def analyze_module(
    tree: ast.AST,
    *,
    monkeypatch_patterns: Collection[str] = (),
) -> ModuleFacts:
    """Return every fact the detectors need from ONE parsed test module.

    Args:
        tree: The parsed module.
        monkeypatch_patterns: The dotted names of the patch helpers whose
            first argument names the patched target.

    Returns:
        The white-box counts, the patched targets, the monkeypatch-bound
        names, the call sites and the declared pytest markers.
    """
    scan = _scan_tree(tree)
    bound = _monkeypatch_names(scan)
    wait_for_identities = _wait_for_call_ids(scan.call_sites)
    call_sites = tuple(
        (node, call_name, id(node) in wait_for_identities) for node, call_name in scan.call_sites
    )
    return ModuleFacts(
        counts=_whitebox_counts(scan, bound),
        patched_targets=frozenset(_patched_targets(scan, bound, monkeypatch_patterns)),
        monkeypatch_names=frozenset(bound),
        call_sites=call_sites,
        markers=frozenset(_collect_markers(scan)),
        has_subprocess_e2e_marker=_has_subprocess_e2e_marker(scan),
    )


def count_setattr_private(tree: ast.AST) -> int:
    """Count monkeypatch calls that target a private production attribute."""
    return analyze_module(tree).counts["setattr_private"]


def count_private_imports(tree: ast.AST) -> int:
    """Count imports that reach private ``ralph`` production internals."""
    return analyze_module(tree).counts["private_import"]


def count_private_attrs(tree: ast.AST) -> int:
    """Count private attribute accesses in high-confidence test contexts."""
    return analyze_module(tree).counts["private_attr"]


def collect_whitebox_counts(tree: ast.AST) -> WhiteboxCounts:
    """Return all white-box detector counts for one parsed test module."""
    return analyze_module(tree).counts


def collect_patched_targets(
    tree: ast.AST,
    monkeypatch_patterns: Collection[str],
) -> set[str]:
    """Collect exact dotted targets patched in ``tree``."""
    return set(analyze_module(tree, monkeypatch_patterns=monkeypatch_patterns).patched_targets)
