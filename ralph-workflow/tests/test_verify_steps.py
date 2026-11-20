from __future__ import annotations

import inspect
import tomllib
from pathlib import Path

import ralph.verify
import ralph.verify_steps

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "_EXPECTED_CPU_BUDGET_SECONDS pins the raised 1800 core-s budget": (
        "the operator raised the suite CPU budget from 1400 to 1800 core-s; the pin still asserts the exact constant"
    ),
    "_EXPECTED_BUDGET_SECONDS and _EXPECTED_HANG_CAP_SECONDS pin 75 s and 225 s": (
        "the wall budget was raised from 60 to 75 s and the hang cap stays three times it (225 s); the test still pins both exact constants and the 3x relation"
    ),
    "_EXPECTED_CPU_BUDGET_SECONDS pins the raised 1400 core-s budget": (
        "the operator raised the suite CPU budget from 700 to 1400 core-s; the pin still asserts the exact constant"
    ),
}

_EXPECTED_BUDGET_SECONDS = 75.0
_EXPECTED_CPU_BUDGET_SECONDS = 1800.0
_EXPECTED_HANG_CAP_SECONDS = 225.0
_EXPECTED_STEP_TIMEOUT_SECONDS = 30.0
_EXPECTED_TEST_STEP_LABELS = frozenset({"make test", "make test-verification-smoke"})

_DEAD_CODE_LABEL = "dead code audit (vulture)"
_DEAD_CODE_COMMAND = "uv run --with vulture python -m vulture --config pyproject.toml"
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_MAKEFILE_PATH = _PACKAGE_ROOT / "Makefile"
_PYPROJECT_PATH = _PACKAGE_ROOT / "pyproject.toml"


def _makefile_lines() -> list[str]:
    return _MAKEFILE_PATH.read_text(encoding="utf-8").splitlines()


def _makefile_target_body(name: str) -> list[str]:
    body: list[str] = []
    in_target = False

    for line in _makefile_lines():
        if not in_target:
            if line.startswith(f"{name}:"):
                in_target = True
            continue
        if not line.startswith("\t"):
            break
        body.append(line.strip())

    assert body, f"target {name!r} not found"

    return body


def test_dead_code_audit_is_registered_in_the_default_gate() -> None:
    steps = ralph.verify_steps._VERIFY_STEPS
    matches = [step for step in steps if step[0] == _DEAD_CODE_LABEL]
    step_timeout = ralph.verify_steps._VERIFY_STEP_TIMEOUT_SECONDS

    assert len(matches) == 1
    label, command, args, timeout = matches[0]
    assert label == _DEAD_CODE_LABEL
    assert (command, args) == (
        "uv",
        ("run", "--with", "vulture", "python", "-m", "vulture", "--config", "pyproject.toml"),
    )
    assert timeout == step_timeout


def test_dead_code_gate_and_makefile_target_run_the_same_command() -> None:
    steps = ralph.verify_steps._VERIFY_STEPS
    matches = [step for step in steps if step[0] == _DEAD_CODE_LABEL]
    _label, command, args, _timeout = matches[0]
    recipe = " ".join(_makefile_target_body("dead-code"))
    phony_line = next(line for line in _makefile_lines() if line.startswith(".PHONY:"))

    assert " ".join((command, *args)) == recipe
    assert recipe == _DEAD_CODE_COMMAND
    assert "dead-code" in phony_line.split()


def test_dead_code_target_scans_the_paths_declared_by_the_vulture_config() -> None:
    config = tomllib.loads(_PYPROJECT_PATH.read_text(encoding="utf-8"))
    vulture_paths = config["tool"]["vulture"]["paths"]
    recipe = " ".join(_makefile_target_body("dead-code"))

    assert "--config pyproject.toml" in recipe
    assert vulture_paths
    for path in vulture_paths:
        assert (_PACKAGE_ROOT / path).is_dir()


def test_dead_code_gate_keeps_the_budget_tracked_steps_intact() -> None:
    steps = ralph.verify_steps._VERIFY_STEPS
    tracked = ralph.verify._BUDGET_TRACKED_STEPS

    assert steps[2][0] == "make test"
    assert steps[len(steps) - 2][0] == "make test-verification-smoke"
    assert _DEAD_CODE_LABEL not in {steps[index][0] for index in tracked}


def test_verify_reexports_the_step_registry() -> None:
    reexported_steps = ralph.verify._VERIFY_STEPS
    registry_steps = ralph.verify_steps._VERIFY_STEPS

    assert reexported_steps is registry_steps


def test_registry_entries_have_the_documented_shape() -> None:
    steps = ralph.verify_steps._VERIFY_STEPS

    for label, command, args, timeout in steps:
        assert isinstance(label, str)
        assert label
        assert isinstance(command, str)
        assert command
        assert isinstance(args, tuple)
        assert all(isinstance(arg, str) for arg in args)
        assert isinstance(timeout, float)
        assert timeout > 0


def test_budget_constants_stay_pinned() -> None:
    budget = ralph.verify._TOTAL_TEST_BUDGET_SECONDS
    cpu_budget = ralph.verify._TOTAL_TEST_CPU_BUDGET_SECONDS
    hang_cap = ralph.verify._TOTAL_TEST_HANG_CAP_SECONDS
    step_timeout = ralph.verify._VERIFY_STEP_TIMEOUT_SECONDS

    assert budget == _EXPECTED_BUDGET_SECONDS
    assert cpu_budget == _EXPECTED_CPU_BUDGET_SECONDS
    assert hang_cap == _EXPECTED_HANG_CAP_SECONDS
    assert hang_cap == 3 * budget
    assert step_timeout == _EXPECTED_STEP_TIMEOUT_SECONDS


def test_registry_timeout_constants_match_the_policy_pins() -> None:
    registry_step_timeout = ralph.verify_steps._VERIFY_STEP_TIMEOUT_SECONDS
    registry_hang_cap = ralph.verify_steps._TOTAL_TEST_HANG_CAP_SECONDS
    pinned_step_timeout = ralph.verify._VERIFY_STEP_TIMEOUT_SECONDS
    pinned_hang_cap = ralph.verify._TOTAL_TEST_HANG_CAP_SECONDS

    assert registry_step_timeout == pinned_step_timeout
    assert registry_hang_cap == pinned_hang_cap


def test_labels_and_tracked_steps_stay_in_sync() -> None:
    steps = ralph.verify_steps._VERIFY_STEPS
    known_labels = ralph.verify._KNOWN_TEST_STEP_LABELS
    tracked_indices = ralph.verify._BUDGET_TRACKED_STEPS

    tracked_labels = {steps[index][0] for index in tracked_indices}
    assert tracked_labels == known_labels
    assert known_labels == _EXPECTED_TEST_STEP_LABELS
    assert "make test" in known_labels
    for index, (label, *_rest) in enumerate(steps):
        if label in known_labels:
            assert index in tracked_indices
        else:
            assert index not in tracked_indices


def test_step_labels_are_unique_and_keep_their_order() -> None:
    steps = ralph.verify_steps._VERIFY_STEPS
    labels = [step[0] for step in steps]

    assert len(labels) == len(set(labels))
    assert labels[0] == "ruff check ralph/ tests/"
    assert labels.index("make test") < labels.index("make test-verification-smoke")


def test_registry_module_exposes_the_step_tuple_type() -> None:
    step_type = ralph.verify_steps.VerifyStep

    assert step_type == tuple[str, str, tuple[str, ...], float | None]


def test_registry_module_defines_data_only() -> None:
    namespace = vars(ralph.verify_steps)
    behaviour = [
        name
        for name, value in namespace.items()
        if inspect.isfunction(value) or inspect.isclass(value)
    ]

    assert behaviour == []
    assert ralph.verify not in list(namespace.values())
