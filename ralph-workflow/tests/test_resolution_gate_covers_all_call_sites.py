from __future__ import annotations

import ast
from pathlib import Path

import pytest

import ralph
from ralph.pipeline.auto_integrate_record import read_record

_TRIGGER_NAME = "StopResolutionTrigger"
_RECORD_POSITION = 4


def _pipeline_sources() -> list[Path]:
    pipeline_root = Path(ralph.__file__).resolve().parent / "pipeline"
    return sorted(path for path in pipeline_root.rglob("*.py") if "__pycache__" not in path.parts)


def _trigger_constructions(path: Path) -> list[ast.Call]:
    source = path.read_text(encoding="utf-8")
    if f"{_TRIGGER_NAME}(" not in source:
        return []
    tree = ast.parse(source, filename=str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == _TRIGGER_NAME
    ]


def _record_expression(call: ast.Call) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == "record":
            return keyword.value
    if len(call.args) > _RECORD_POSITION:
        return call.args[_RECORD_POSITION]
    return None


def _is_absent_record(expression: ast.expr | None) -> bool:
    if expression is None:
        return True
    return isinstance(expression, ast.Constant) and expression.value is None


def test_every_stop_resolution_trigger_supplies_a_durable_record() -> None:
    sources = _pipeline_sources()
    assert sources, "no pipeline modules were discovered"

    missing: list[str] = []
    constructions = 0
    for path in sources:
        for call in _trigger_constructions(path):
            constructions += 1
            if _is_absent_record(_record_expression(call)):
                missing.append(f"{path.name}:{call.lineno}")

    assert constructions >= 3, (
        f"expected the pipeline to construct at least three {_TRIGGER_NAME} call sites, "
        f"found {constructions}"
    )
    assert not missing, (
        "every StopResolutionTrigger must supply a durable record so the resolution "
        f"retry gate applies: {missing}"
    )


@pytest.mark.timeout_seconds(10)
def test_merge_door_persists_the_gate_key_and_blocks_the_repeated_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ralph.pipeline import auto_integrate_resolve

    monkeypatch.setattr(auto_integrate_resolve, "unmerged_paths", lambda _root: ["conflict.py"])
    monkeypatch.setattr(auto_integrate_resolve, "staged_conflict_marker_paths", lambda _root: [])

    launched: list[str] = []

    def resolver(_root: Path, target: str) -> bool:
        launched.append(target)
        return False

    first = auto_integrate_resolve.resolve_and_commit_with_reason(tmp_path, "main", resolver)

    assert launched == ["main"]
    assert first.outcome == auto_integrate_resolve.MERGE_COMMIT_PENDING

    record = read_record(tmp_path)
    assert record is not None
    assert record.resolution_gate_key is not None

    second = auto_integrate_resolve.resolve_and_commit_with_reason(tmp_path, "main", resolver)

    assert launched == ["main"], "the retry gate must not launch the resolver a second time"
    assert second.outcome == auto_integrate_resolve.MERGE_COMMIT_PENDING
    assert (second.reason or "").startswith("WAITING:")
