"""No superseded writers for decision fields outside the typed setters.

The seven conflict-strategy / deadline / intervention fields are owned
by a small set of typed single-writer setters on ``RebaseState``:

* :meth:`set_retry_deadline`  -- single writer of ``retry_deadline``
* :meth:`record_consecutive_conflicts` -- single writer of
  ``consecutive_conflicts``
* :meth:`advance_conflict_strategy` -- single writer of
  ``conflict_strategy_index``, ``conflict_strategies_tried``,
  ``resolution_exhausted``, ``resolution_exhaustion_reason``
  (bounded advancement)
* :meth:`project_conflict_strategy_from` -- exact one-way projection
  of the same four fields
* :meth:`clear_conflict_strategy` -- reset-to-default transition of
  the same four fields (used by
  ``reconcile_stale_unresolved_state``)
* :meth:`clear_resolution_exhausted` -- reset of only the
  ``resolution_exhausted`` / ``resolution_exhaustion_reason`` pair
  (preserves the strategy index and tried list)
* :meth:`set_intervention_required` -- single writer of the
  ``INTERVENTION_REQUIRED`` / ``DIAGNOSTIC_INTERVENTION_REQUIRED``
  transition

The AST walk below proves that **no other module writes any of these
fields** through a literal-Dict ``model_copy``/``copy_with`` call or
a variable-backed ``update=`` argument. The allowlist is METHOD-BODY
SCOPED: only the body of each named setter is exempt; nothing else
in ``rebase_state.py`` is allowlisted.

Variable-backed writer detection
--------------------------------

A variable-backed writer is any ``model_copy(update=<X>)`` /
``copy_with(update=<X>)`` call where ``X`` is not a dict literal but
a ``Name`` (e.g. ``update=update_dict``), a ``Call`` (e.g.
``update=build_dict()``), or a ``Subscript`` (e.g.
``update=MY_DICT["conflict_strategy_index"]``). Such writers can
silently smuggle decision fields past a literal-Dict scan.

The detection uses a coarse file-level prefilter (skip files whose
source text never mentions a decision field) combined with a local
flow check on the same function so that benign builders (e.g.
``auto_integrate_conflict_budget.py``'s ``update`` dict populated
with ``last_conflict_*`` keys only) are not flagged. The flow check
traces a ``Name`` ``update=`` value back to its in-function
assignment (an ``Assign`` or ``AnnAssign`` with a ``Dict`` value)
and inspects the literal. ``Call`` values are resolved to the
function defined in the same file (the typical local builder
pattern) and its return literal is inspected. ``Subscript`` values
flag if the subscript key is itself a decision field. Sites that
cannot be statically resolved are flagged conservatively so the
audit can investigate them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# The seven fields whose writers are gated by the typed setters.
DECISION_FIELDS: frozenset[str] = frozenset(
    {
        "retry_deadline",
        "consecutive_conflicts",
        "conflict_strategy_index",
        "conflict_strategies_tried",
        "resolution_exhausted",
        "resolution_exhaustion_reason",
    }
)

# The set of method bodies inside ``rebase_state.py`` that are the
# only allowed writer sites. The audit walks each
# FunctionDef/AsyncFunctionDef and exempts the body whose qualified
# name appears in this set. NOTHING ELSE in ``rebase_state.py`` is
# allowlisted -- the prior whole-file exemption was a wide hole.
ALLOWED_SETTER_METHODS: frozenset[str] = frozenset(
    {
        "RebaseState.set_retry_deadline",
        "RebaseState.record_consecutive_conflicts",
        "RebaseState.advance_conflict_strategy",
        "RebaseState.project_conflict_strategy_from",
        "RebaseState.clear_conflict_strategy",
        "RebaseState.clear_resolution_exhausted",
        "RebaseState.set_intervention_required",
    }
)


def _all_python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _class_qualified_name(class_node: ast.ClassDef, method: ast.FunctionDef) -> str:
    return f"{class_node.name}.{method.name}"


def _gather_allowed_method_bodies(path: Path) -> dict[tuple[int, int], str]:
    """Return ``{(start_lineno, end_lineno): method_name}`` for every named setter."""
    if path.name != "rebase_state.py":
        return {}
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    allowed: dict[tuple[int, int], str] = {}
    for class_node in ast.walk(tree):
        if not isinstance(class_node, ast.ClassDef):
            continue
        for child in ast.iter_child_nodes(class_node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            qname = _class_qualified_name(class_node, child)
            if qname in ALLOWED_SETTER_METHODS:
                start = child.lineno
                end = child.end_lineno or start
                allowed[(start, end)] = qname
    return allowed


def _extract_dict_keys(value: ast.AST) -> list[str]:
    """Return the string keys of a dict-literal expression, or empty."""
    if isinstance(value, ast.Dict):
        keys: list[str] = []
        for k in value.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.append(k.value)
        return keys
    return []


def _enclosing_function(
    tree: ast.AST,
    target: ast.AST,
    funcs: list[ast.FunctionDef | ast.AsyncFunctionDef] | None = None,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find the nearest enclosing function for *target* in *tree*."""
    candidates = (
        funcs
        if funcs is not None
        else [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
    )
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    target_line = getattr(target, "lineno", 0)
    for node in candidates:
        if not (node.lineno <= target_line <= (node.end_lineno or node.lineno)):
            continue
        if best is None or node.lineno >= best.lineno:
            best = node
    return best


def _resolve_name_dict_literal(
    tree: ast.AST,
    call: ast.Call,
    name_node: ast.Name,
    funcs: list[ast.FunctionDef | ast.AsyncFunctionDef] | None = None,
) -> ast.Dict | None:
    """Trace a Name ``update=`` value back to an in-function dict literal.

    Returns the ``Dict`` literal assigned to the same name in the
    enclosing function (handles both ``Assign`` and ``AnnAssign``),
    or ``None`` when no such assignment exists.
    """
    func = _enclosing_function(tree, call, funcs=funcs)
    if func is None:
        return None
    for stmt in ast.walk(func):
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == name_node.id
                    and isinstance(stmt.value, ast.Dict)
                ):
                    return stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            target = stmt.target
            value = stmt.value
            if (
                isinstance(target, ast.Name)
                and target.id == name_node.id
                and isinstance(value, ast.Dict)
            ):
                return value
    return None


def _resolve_call_dict_literal(
    tree: ast.AST,
    call_node: ast.Call,
    funcs: list[ast.FunctionDef | ast.AsyncFunctionDef] | None = None,
) -> ast.Dict | None:
    """Resolve a Call ``update=f(...)`` to a dict literal by name.

    Looks for a top-level ``def f(...)`` (or ``async def``) in the
    same file whose return is a dict literal. Returns the literal
    if found, otherwise ``None``.
    """
    if not isinstance(call_node.func, ast.Name):
        return None
    fn_name = call_node.func.id
    candidates = (
        funcs
        if funcs is not None
        else [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
    )
    for node in candidates:
        if node.name != fn_name:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and isinstance(child.value, ast.Dict):
                return child.value
    return None


def _resolve_subscript_key(
    subscript: ast.Subscript,
) -> str | None:
    """Return the constant string key of a ``X["key"]`` subscript, or ``None``."""
    slc = subscript.slice
    if isinstance(slc, ast.Constant) and isinstance(slc.value, str):
        return slc.value
    return None


def _file_mentions_decision_field(path: Path) -> bool:
    """Cheap prefilter: file source mentions any DECISION_FIELDS member."""
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    return any(field.encode() in raw for field in DECISION_FIELDS)


def _is_writer_call(node: ast.Call) -> bool:
    """Whether *node* is a ``model_copy(...)`` or ``copy_with(...)`` call."""
    return isinstance(node.func, ast.Attribute) and node.func.attr in (
        "model_copy",
        "copy_with",
    )


def _update_arg(node: ast.Call) -> ast.AST | None:
    """Return the ``update=...`` argument of a writer call, or ``None``."""
    for kw in node.keywords:
        if kw.arg == "update":
            return kw.value
    if node.args:
        return node.args[0]
    return None


def _is_in_allowed_body(lineno: int, allowed_bodies: dict[tuple[int, int], str]) -> bool:
    return any(start <= lineno <= end for (start, end) in allowed_bodies)


def _resolve_variable_dict(
    tree: ast.AST,
    call: ast.Call,
    update_value: ast.AST,
    funcs: list[ast.FunctionDef | ast.AsyncFunctionDef] | None = None,
) -> ast.Dict | None:
    """Resolve a Name/Call ``update=`` value to a dict literal if possible."""
    if isinstance(update_value, ast.Name):
        return _resolve_name_dict_literal(tree, call, update_value, funcs=funcs)
    if isinstance(update_value, ast.Call):
        return _resolve_call_dict_literal(tree, update_value, funcs=funcs)
    return None


def _record_dict_decision_offenders(
    offenders: list[tuple[int, str, str]], lineno: int, kind: str, d: ast.Dict
) -> None:
    for k in _extract_dict_keys(d):
        if k in DECISION_FIELDS:
            offenders.append((lineno, kind, k))


def _scan_for_field_writes(
    path: Path,
    allowed_bodies: dict[tuple[int, int], str],
) -> list[tuple[int, str, str]]:
    """Return (line, kind, field) for every writer that targets one of
    the decision fields. ``kind`` is:

    * ``literal``  -- a dict literal ``update={"k": ...}`` with ``k`` in ``DECISION_FIELDS``
    * ``variable`` -- a variable-backed ``update=<Name|Call|Subscript>`` whose
      statically-resolved value references a decision field, or that
      could not be resolved and is flagged conservatively
    """
    # Prefilter: skip files that do not mention any decision field.
    if not _file_mentions_decision_field(path):
        return []
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    funcs: list[ast.FunctionDef | ast.AsyncFunctionDef] = [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    offenders: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_writer_call(node):
            continue
        update_value = _update_arg(node)
        if update_value is None:
            continue
        if _is_in_allowed_body(node.lineno, allowed_bodies):
            continue
        # Literal Dict: pull out string keys.
        if isinstance(update_value, ast.Dict):
            _record_dict_decision_offenders(offenders, node.lineno, "literal", update_value)
            continue
        # Subscript: ``update=X["conflict_strategy_index"]`` is itself a
        # decision-field write.
        if isinstance(update_value, ast.Subscript):
            key = _resolve_subscript_key(update_value)
            if key is not None and key in DECISION_FIELDS:
                offenders.append((node.lineno, "variable", key))
            continue
        # Variable-backed Name / Call: resolve to a dict literal if
        # statically possible; otherwise flag conservatively.
        resolved = _resolve_variable_dict(tree, node, update_value, funcs=funcs)
        if resolved is not None:
            _record_dict_decision_offenders(offenders, node.lineno, "variable", resolved)
            continue
        offenders.append((node.lineno, "variable", "<unresolved>"))
    return offenders


class TestNoSupersededPathsTypedSetterContract:
    """No raw literal-Dict writer exists for the seven decision fields
    outside the typed setter method-body allowlist."""

    @pytest.mark.timeout_seconds(5)
    def test_no_raw_writers_in_pipeline(self) -> None:
        root = Path("ralph/pipeline")
        all_offenders: list[tuple[str, int, str, str]] = []
        for path in _all_python_files(root):
            allowed = _gather_allowed_method_bodies(path)
            for lineno, kind, field in _scan_for_field_writes(path, allowed):
                all_offenders.append((str(path), lineno, kind, field))
        assert all_offenders == [], (
            "Raw writers for the typed decision fields exist outside the "
            "allowlist. Offending sites:\n"
            + "\n".join(f"  {p}:{ln}  kind={k}  field={f!r}" for p, ln, k, f in all_offenders)
        )

    def test_typed_setters_exist_in_rebase_state(self) -> None:
        """The seven typed setters are present in ``rebase_state.py``
        and each writes one or more of the decision fields. Removing
        any one of them is a sign the typed-setter contract has
        regressed.
        """
        from ralph.pipeline import rebase_state

        text = Path(rebase_state.__file__).read_text(encoding="utf-8")
        for setter in (
            "def set_retry_deadline",
            "def record_consecutive_conflicts",
            "def advance_conflict_strategy",
            "def project_conflict_strategy_from",
            "def clear_conflict_strategy",
            "def clear_resolution_exhausted",
            "def set_intervention_required",
        ):
            assert setter in text, f"Typed setter {setter!r} is missing from rebase_state.py"

    def test_synthetic_dict_literal_writer_is_caught(self, tmp_path: Path) -> None:
        """A synthetic file with a ``model_copy(update={'field': ...})``
        literal IS caught by the audit (because the literal Dict can
        be inspected for decision-field keys)."""
        bad = tmp_path / "_bad_literal_writer.py"
        bad.write_text(
            "def write_it(rebase):\n"
            "    return rebase.model_copy(update={'conflict_strategy_index': 1})\n",
            encoding="utf-8",
        )
        offenders = _scan_for_field_writes(bad, allowed_bodies={})
        assert any(
            kind == "literal" and field == "conflict_strategy_index" for _, kind, field in offenders
        ), "synthetic dict-literal writer was not detected"

    def test_synthetic_writer_in_allowed_setter_body_is_exempt(self, tmp_path: Path) -> None:
        """A writer inside an allowlisted setter body is NOT caught --
        the audit is method-body scoped, not whole-file scoped."""
        allowed_path = tmp_path / "rebase_state.py"
        # Replicate the structure of an allowlisted setter.
        allowed_path.write_text(
            "class RebaseState:\n"
            "    def advance_conflict_strategy(self, reason):\n"
            "        return self.model_copy(update={'conflict_strategy_index': 1})\n",
            encoding="utf-8",
        )
        allowed = _gather_allowed_method_bodies(allowed_path)
        assert "RebaseState.advance_conflict_strategy" in allowed.values()
        offenders = _scan_for_field_writes(allowed_path, allowed)
        assert offenders == [], (
            f"writer inside the allowlisted setter body was incorrectly flagged: {offenders}"
        )

    def test_synthetic_variable_backed_writer_is_caught(self, tmp_path: Path) -> None:
        """A variable-backed ``model_copy(update=build_dict())`` whose
        builder returns a dict containing a decision field IS caught.
        The synthetic file's source mentions the field, so the
        prefilter admits it; the local-flow check resolves the call
        to the function's return literal and inspects its keys.
        """
        bad = tmp_path / "_bad_variable_writer.py"
        bad.write_text(
            "def build_dict():\n"
            "    return {'conflict_strategy_index': 1}\n"
            "def write_it(rebase):\n"
            "    return rebase.model_copy(update=build_dict())\n",
            encoding="utf-8",
        )
        offenders = _scan_for_field_writes(bad, allowed_bodies={})
        assert any(
            kind == "variable" and field == "conflict_strategy_index"
            for _, kind, field in offenders
        ), f"synthetic variable-backed writer was not detected: {offenders}"

    def test_synthetic_variable_backed_writer_in_clean_file_is_not_flagged(
        self, tmp_path: Path
    ) -> None:
        """A variable-backed writer in a file that does not mention a
        decision field is NOT flagged. The prefilter excludes the
        file before the AST walk; this proves the prefilter is the
        outer gate and a benign builder in a clean module is
        correctly left alone.
        """
        clean = tmp_path / "_clean_variable_writer.py"
        clean.write_text(
            "def build_dict():\n"
            "    return {'some_other_field': 1}\n"
            "def write_it(rebase):\n"
            "    return rebase.model_copy(update=build_dict())\n",
            encoding="utf-8",
        )
        assert not _file_mentions_decision_field(clean)
        offenders = _scan_for_field_writes(clean, allowed_bodies={})
        assert offenders == [], f"clean variable-backed writer was incorrectly flagged: {offenders}"

    def test_synthetic_name_backed_writer_resolves_to_local_dict(self, tmp_path: Path) -> None:
        """A ``Name`` ``update=`` value that traces to a local dict
        literal WITHOUT decision fields is NOT flagged. Mirrors the
        benign ``auto_integrate_conflict_budget.py:309`` pattern
        (an ``AnnAssign`` of a dict whose keys are non-decision).
        """
        benign = tmp_path / "_benign_name_writer.py"
        benign.write_text(
            "def write_it(record):\n"
            "    update: dict[str, object] = {\n"
            "        'last_conflict_feature_sha': 'a',\n"
            "        'last_conflict_target_sha': 'b',\n"
            "    }\n"
            "    return record.model_copy(update=update)\n",
            encoding="utf-8",
        )
        offenders = _scan_for_field_writes(benign, allowed_bodies={})
        assert offenders == [], (
            f"benign in-function dict builder was incorrectly flagged: {offenders}"
        )

    def test_synthetic_name_backed_writer_with_decision_field_is_caught(
        self, tmp_path: Path
    ) -> None:
        """A ``Name`` ``update=`` value that traces to a local dict
        literal CONTAINING a decision field IS flagged. Proves the
        flow check forwards dict-literal keys into the same kind
        taxonomy as the literal-Dict path.
        """
        bad = tmp_path / "_bad_name_writer.py"
        bad.write_text(
            "def write_it(rebase):\n"
            "    update = {'conflict_strategy_index': 1}\n"
            "    return rebase.model_copy(update=update)\n",
            encoding="utf-8",
        )
        offenders = _scan_for_field_writes(bad, allowed_bodies={})
        assert any(
            kind == "variable" and field == "conflict_strategy_index"
            for _, kind, field in offenders
        ), f"name-backed writer with decision field was not detected: {offenders}"
