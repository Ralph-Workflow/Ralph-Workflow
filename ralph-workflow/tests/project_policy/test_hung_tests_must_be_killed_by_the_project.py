from __future__ import annotations

import re

import pytest

from ralph.project_policy import markers, starters
from ralph.project_policy.validators import check_fixed_time_caps

_TESTING = "testing-policy.md"
_PATH = f"{markers.CANONICAL_DIR}{_TESTING}"


def _with_fact(value: str) -> str:
    body = starters.read_starter(_TESTING)
    return re.sub(
        r"RALPH-FACT: timeout_enforcement_mechanism: .*",
        f"RALPH-FACT: timeout_enforcement_mechanism: {value}",
        body,
        count=1,
    )


def _timeout_findings(value: str) -> list[str]:
    return [
        f.requirement_id
        for f in check_fixed_time_caps(_with_fact(value), _PATH, _TESTING)
        if f.requirement_id.endswith(":timeout-enforcement-missing")
    ]


@pytest.mark.parametrize(
    "value",
    [
        "none — no timeout plugin is installed, so the suite budget review catches a slow suite",
        "None",
        "n/a",
        "not configured",
    ],
)
def test_a_suite_with_no_runner_timeout_is_not_ready(value: str) -> None:
    expected = f"{markers.ID_TIME_BUDGET}:{_TESTING}:timeout-enforcement-missing"
    assert _timeout_findings(value) == [expected]


def test_a_suite_whose_runner_kills_a_hung_test_is_ready() -> None:
    assert _timeout_findings("pytest-timeout --timeout=10 per test, SIGKILL on expiry") == []


def test_the_starter_says_the_limit_must_stop_the_run() -> None:
    text = " ".join(starters.read_starter(_TESTING).split())
    assert "TERMINATES the run when exceeded" in text
    assert "only compares elapsed time after the command exits" in text
