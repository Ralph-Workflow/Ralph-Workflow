from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest
from loguru import logger

import ralph
from ralph.git.operations import get_head_sha
from ralph.pipeline import run_loop
from ralph.testing import audit_integration_invariants

_PROJECT_ROOT = Path(ralph.__file__).resolve().parent.parent
_GIT_ROOT = _PROJECT_ROOT.parent
_PIPELINE_PREFIX = "ralph/pipeline/"
_ADAPTER_RELATIVE_PATH = "ralph/pipeline/_terminal_emitter.py"
_DECLARED_EMITTER_FILES = (
    "ralph/pipeline/_auto_integrate_once.py",
    "ralph/pipeline/_integration_diagnostic.py",
    "ralph/pipeline/_integration_diagnostic_prepass.py",
    "ralph/pipeline/_pending_merge_repair.py",
    "ralph/pipeline/auto_integrate_recovery_legacy.py",
    "ralph/pipeline/auto_integrate_recovery_record.py",
    "ralph/pipeline/run_loop.py",
)
_MID_MIGRATION_DIRECT_CALL_BUDGET = {
    "ralph/pipeline/_integration_diagnostic.py": 8,
    "ralph/pipeline/auto_integrate_recovery_record.py": 2,
    "ralph/pipeline/run_loop.py": 3,
}
_COMMIT_TOKEN = re.compile(r"[0-9a-f]{8}|unknown")


@dataclass(frozen=True)
class _FileScan:
    direct_critical_lines: tuple[int, ...]
    references_terminal_emitter: bool


@cache
def _modules() -> dict[str, str]:
    return audit_integration_invariants.load_modules(_PROJECT_ROOT)


def _pipeline_relatives() -> tuple[str, ...]:
    return tuple(path for path in sorted(_modules()) if path.startswith(_PIPELINE_PREFIX))


@cache
def _critical_candidate_relatives() -> tuple[str, ...]:
    candidates = [
        relative for relative in _pipeline_relatives() if "critical" in _modules()[relative]
    ]
    return tuple(candidates)


def _is_logger_receiver(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "logger" or node.id.endswith("logger")
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "opt"
    )


@cache
def _scan(relative: str) -> _FileScan:
    tree = ast.parse(_modules()[relative], filename=relative)
    direct: list[int] = []
    references = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "critical" and _is_logger_receiver(node.func.value):
                direct.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.module.endswith("_terminal_emitter"):
                references = True
        elif isinstance(node, ast.Import):
            if any(alias.name.endswith("_terminal_emitter") for alias in node.names):
                references = True
        elif isinstance(node, ast.Name):
            if node.id.startswith("terminal_emitter"):
                references = True
        elif isinstance(node, ast.Attribute) and node.attr.startswith("terminal_emitter"):
            references = True
    return _FileScan(
        direct_critical_lines=tuple(sorted(direct)),
        references_terminal_emitter=references,
    )


def test_pipeline_critical_emission_is_confined_to_the_declared_inventory() -> None:
    undeclared: dict[str, tuple[int, ...]] = {}
    over_budget: dict[str, tuple[int, ...]] = {}
    for relative in _critical_candidate_relatives():
        if relative == _ADAPTER_RELATIVE_PATH:
            continue
        calls = _scan(relative).direct_critical_lines
        if not calls:
            continue
        if relative not in _DECLARED_EMITTER_FILES:
            undeclared[relative] = calls
            continue
        budget = _MID_MIGRATION_DIRECT_CALL_BUDGET.get(relative, 0)
        if len(calls) > budget:
            over_budget[relative] = calls

    assert undeclared == {}, (
        f"pipeline CRITICAL emissions must go through {_ADAPTER_RELATIVE_PATH}; "
        f"undeclared emitters: {undeclared}"
    )
    assert over_budget == {}, (
        f"declared emitter files must not call logger.critical directly; over budget: {over_budget}"
    )


def test_migrated_emitter_files_route_critical_through_the_shared_adapter() -> None:
    offenders: list[str] = []
    for relative in _DECLARED_EMITTER_FILES:
        assert relative in _modules(), f"declared emitter {relative} does not exist"
        if _scan(relative).direct_critical_lines:
            continue
        if not _scan(relative).references_terminal_emitter:
            offenders.append(relative)

    assert offenders == [], (
        "declared emitter files without direct logger.critical calls must emit through "
        f"{_ADAPTER_RELATIVE_PATH}; offenders: {offenders}"
    )


def test_shared_adapter_is_the_only_direct_critical_emitter() -> None:
    adapter_calls = _scan(_ADAPTER_RELATIVE_PATH).direct_critical_lines

    assert adapter_calls, f"{_ADAPTER_RELATIVE_PATH} must emit the pipeline CRITICAL line"


def test_pipeline_critical_candidates_cover_every_pipeline_module() -> None:
    relatives = _pipeline_relatives()

    assert relatives, "the pipeline package must contain modules"
    assert set(_critical_candidate_relatives()) <= set(relatives)


def test_adapter_output_carries_package_version_and_commit_sha() -> None:
    messages: list[str] = []
    sink_id = logger.add(
        lambda message: messages.append(str(message)),
        level="DEBUG",
        format="{message}",
    )
    try:
        run_loop.critical("inventory probe")
    finally:
        logger.remove(sink_id)

    assert len(messages) == 1, f"expected exactly one rendered line, got {messages}"
    rendered = messages[0]
    assert f"v{ralph.__version__}+" in rendered, rendered
    commit = rendered.rsplit("+", 1)[-1].strip().rstrip("]")
    assert _COMMIT_TOKEN.fullmatch(commit), rendered
    assert get_head_sha(_GIT_ROOT)[:8] in rendered, rendered


def test_adapter_output_carries_identity_with_arguments() -> None:
    messages: list[str] = []
    sink_id = logger.add(
        lambda message: messages.append(str(message)),
        level="DEBUG",
        format="{message}",
    )
    try:
        run_loop.critical("inventory probe {} {}", "alpha", "beta")
    finally:
        logger.remove(sink_id)

    assert len(messages) == 1, f"expected exactly one rendered line, got {messages}"
    rendered = messages[0]
    assert "inventory probe alpha beta" in rendered, rendered
    assert f"v{ralph.__version__}+" in rendered, rendered


@pytest.mark.parametrize("relative", _DECLARED_EMITTER_FILES)
def test_declared_emitter_files_exist(relative: str) -> None:
    assert relative in _modules(), f"declared emitter {relative} does not exist"
