from __future__ import annotations

import re

import pytest

from ralph.project_policy import markers, starters
from ralph.project_policy.validators import check_fixed_time_caps

_VERIFICATION = "verification-policy.md"
_TESTING = "testing-policy.md"


def _with(filename: str, key: str, value: str) -> str:
    return re.sub(
        rf"RALPH-FACT: {key}: .*",
        f"RALPH-FACT: {key}: {value}",
        starters.read_starter(filename),
        count=1,
    )


def _ids(filename: str, content: str) -> list[str]:
    path = f"{markers.CANONICAL_DIR}{filename}"
    return [f.requirement_id for f in check_fixed_time_caps(content, path, filename)]


@pytest.mark.parametrize(
    ("filename", "key", "value"),
    [
        (_VERIFICATION, "verification_enforced_limit", "30 seconds (VERIFY_BUDGET_SECONDS)"),
        (_VERIFICATION, "fast_path_enforced_limit", "20s"),
        (_TESTING, "suite_enforced_limit", "25 seconds"),
    ],
)
def test_a_limit_at_the_measured_figure_is_rejected(filename: str, key: str, value: str) -> None:
    assert f"{markers.ID_TIME_BUDGET}:{filename}:{key}-below-cap" in _ids(
        filename, _with(filename, key, value)
    )


@pytest.mark.parametrize(
    ("filename", "key", "value"),
    [
        (_VERIFICATION, "verification_enforced_limit", "124 seconds (3.87 s floor + 120 s cap)"),
        (_VERIFICATION, "verification_enforced_limit", "3 minutes"),
        (_VERIFICATION, "fast_path_enforced_limit", "34s (floor 3.87 s + 30 s)"),
        (_TESTING, "suite_enforced_limit", "64 seconds"),
    ],
)
def test_a_limit_at_or_above_the_cap_is_accepted(filename: str, key: str, value: str) -> None:
    assert not any(
        i.endswith(f"{key}-below-cap") for i in _ids(filename, _with(filename, key, value))
    )


def test_a_deferred_hang_guard_is_not_ready() -> None:
    content = _with(
        _TESTING,
        "timeout_enforcement_mechanism",
        "RALPH-PENDING (assumed 2026-11-25); review trigger: the owner installs one",
    )
    assert f"{markers.ID_TIME_BUDGET}:{_TESTING}:timeout-enforcement-missing" in _ids(
        _TESTING, content
    )


def test_the_starters_require_the_enforced_limits() -> None:
    verification = starters.read_starter(_VERIFICATION)
    testing = starters.read_starter(_TESTING)
    assert "RALPH-FACT: verification_enforced_limit:" in verification
    assert "RALPH-FACT: fast_path_enforced_limit:" in verification
    assert "RALPH-FACT: suite_enforced_limit:" in testing
