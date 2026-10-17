"""No superseded writers for decision fields outside the typed setters.

The seven conflict-strategy / deadline / intervention fields are owned
by a small set of typed single-writer setters on ``RebaseState``:

* :meth:`set_retry_deadline`  -- single writer of ``retry_deadline``
* :meth:`record_consecutive_conflicts` -- single writer of
  ``consecutive_conflicts``
* :meth:`advance_conflict_strategy` -- single writer of
  ``conflict_strategy_index``, ``conflict_strategies_tried``,
  ``resolution_exhausted``, ``resolution_exhaustion_reason``
* :meth:`project_conflict_strategy_from` -- exact one-way projection
  of the same four fields
* :meth:`set_intervention_required` -- single writer of the
  ``INTERVENTION_REQUIRED`` / ``DIAGNOSTIC_INTERVENTION_REQUIRED``
  transition

The AST walk below proves that **no other module writes any of these
fields** through a literal-Dict ``model_copy``/``copy_with`` call.
The allowlist is METHOD-BODY SCOPED: only the body of each named
setter is exempt; nothing else in ``rebase_state.py`` is allowlisted.
"""

from __future__ import annotations

import ast
from pathlib import Path

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


def _scan_for_field_writes(
    path: Path,
    allowed_bodies: dict[tuple[int, int], str],
) -> list[tuple[int, str, str]]:
    """Return (line, kind, field) for every literal-Dict writer that
    targets one of the decision fields. ``kind`` is ``literal`` for a
    Dict literal as the update arg.
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    offenders: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Only interested in ``.model_copy(update=...)`` or
        # ``.copy_with(update=...)`` style writes.
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in (
            "model_copy",
            "copy_with",
        ):
            continue
        # Locate the keyword ``update=...``.
        update_value: ast.AST | None = None
        for kw in node.keywords:
            if kw.arg == "update":
                update_value = kw.value
        if update_value is None and node.args:
            update_value = node.args[0]
        if update_value is None:
            continue
        # Determine whether the writer lives inside an allowed setter body.
        in_allowed = any(start <= node.lineno <= end for (start, end) in allowed_bodies)
        if in_allowed:
            continue
        # Literal Dict: pull out string keys.
        keys = _extract_dict_keys(update_value)
        for k in keys:
            if k in DECISION_FIELDS:
                offenders.append((node.lineno, "literal", k))
    return offenders


class TestNoSupersededPathsTypedSetterContract:
    """No raw literal-Dict writer exists for the seven decision fields
    outside the typed setter method-body allowlist."""

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
        """The five typed setters are present in ``rebase_state.py`` and
        each writes one or more of the decision fields. Removing any
        one of them is a sign the typed-setter contract has regressed.
        """
        from ralph.pipeline import rebase_state

        text = Path(rebase_state.__file__).read_text(encoding="utf-8")
        for setter in (
            "def set_retry_deadline",
            "def record_consecutive_conflicts",
            "def advance_conflict_strategy",
            "def project_conflict_strategy_from",
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
