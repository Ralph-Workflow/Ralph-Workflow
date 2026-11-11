from __future__ import annotations

import ast
from pathlib import Path

import pytest

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

ALLOWED_SETTER_METHODS: frozenset[str] = frozenset(
    {
        "RebaseState.set_retry_deadline",
        "RebaseState.record_consecutive_conflicts",
        "RebaseState.advance_conflict_strategy",
        "RebaseState.project_conflict_strategy_from",
        "RebaseState.clear_conflict_strategy",
        "RebaseState.clear_resolution_exhausted",
        "RebaseState.set_ladder_escalation",
    }
)


def _all_python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _class_qualified_name(class_node: ast.ClassDef, method: ast.FunctionDef) -> str:
    return f"{class_node.name}.{method.name}"


def _gather_allowed_method_bodies(path: Path) -> dict[tuple[int, int], str]:
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
    slc = subscript.slice
    if isinstance(slc, ast.Constant) and isinstance(slc.value, str):
        return slc.value
    return None


def _file_mentions_decision_field(path: Path) -> bool:
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    return any(field.encode() in raw for field in DECISION_FIELDS)


def _is_writer_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr in (
        "model_copy",
        "copy_with",
    )


def _update_arg(node: ast.Call) -> ast.AST | None:
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
        if isinstance(update_value, ast.Dict):
            _record_dict_decision_offenders(offenders, node.lineno, "literal", update_value)
            continue
        if isinstance(update_value, ast.Subscript):
            key = _resolve_subscript_key(update_value)
            if key is not None and key in DECISION_FIELDS:
                offenders.append((node.lineno, "variable", key))
            continue
        resolved = _resolve_variable_dict(tree, node, update_value, funcs=funcs)
        if resolved is not None:
            _record_dict_decision_offenders(offenders, node.lineno, "variable", resolved)
            continue
        offenders.append((node.lineno, "variable", "<unresolved>"))
    return offenders


class TestNoSupersededPathsTypedSetterContract:
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
        from ralph.pipeline import rebase_state

        text = Path(rebase_state.__file__).read_text(encoding="utf-8")
        for setter in (
            "def set_retry_deadline",
            "def record_consecutive_conflicts",
            "def advance_conflict_strategy",
            "def project_conflict_strategy_from",
            "def clear_conflict_strategy",
            "def clear_resolution_exhausted",
            "def set_ladder_escalation",
        ):
            assert setter in text, f"Typed setter {setter!r} is missing from rebase_state.py"

    def test_synthetic_dict_literal_writer_is_caught(self, tmp_path: Path) -> None:
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
        allowed_path = tmp_path / "rebase_state.py"
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
