from __future__ import annotations

import inspect

import ralph.verify
import ralph.verify_steps

_EXPECTED_BUDGET_SECONDS = 60.0
_EXPECTED_CPU_BUDGET_SECONDS = 700.0
_EXPECTED_HANG_CAP_SECONDS = 180.0
_EXPECTED_STEP_TIMEOUT_SECONDS = 30.0
_EXPECTED_TEST_STEP_LABELS = frozenset({"make test", "make test-verification-smoke"})


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
