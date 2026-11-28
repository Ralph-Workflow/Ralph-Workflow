from __future__ import annotations

import pytest

from ralph import test_suites
from ralph.verify_timeout import DEFAULT_SUITE_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "arguments", [("--subprocess-e2e",), ("--auto-integrate-e2e",), ("--profile", "unit"), ()]
)
def test_a_busy_host_stretches_the_suite_deadline_like_a_verify_step(
    monkeypatch: pytest.MonkeyPatch, arguments: tuple[str, ...]
) -> None:
    deadlines: list[float] = []

    def _run(**kwargs: object) -> int:
        deadline = kwargs["suite_timeout_seconds"]
        assert isinstance(deadline, float)
        deadlines.append(deadline)
        return 0

    monkeypatch.setattr(test_suites, "host_load_share", lambda: 0.75)
    monkeypatch.setattr(test_suites, "run_test_suites", _run)

    assert test_suites.main(arguments) == 0

    assert deadlines == [DEFAULT_SUITE_TIMEOUT_SECONDS * 4]


def test_an_idle_host_keeps_the_default_suite_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    deadlines: list[float] = []

    def _run(**kwargs: object) -> int:
        deadline = kwargs["suite_timeout_seconds"]
        assert isinstance(deadline, float)
        deadlines.append(deadline)
        return 0

    monkeypatch.setattr(test_suites, "host_load_share", lambda: 0.0)
    monkeypatch.setattr(test_suites, "run_test_suites", _run)

    assert test_suites.main(("--subprocess-e2e",)) == 0

    assert deadlines == [DEFAULT_SUITE_TIMEOUT_SECONDS]
