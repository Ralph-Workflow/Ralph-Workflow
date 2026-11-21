from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.publication_gate import MAX_INCONCLUSIVE_GATE_RUNS
from tests.scenarios._run_soak import TARGET, AgentCall, SoakRun, assert_soak_invariant, git

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(30),
]


def _break_the_target(soak: SoakRun, _call: AgentCall) -> None:
    soak.move_target("broken.flag", "broken\n", "target breaks the build")


@pytest.mark.parametrize(
    ("gate", "cause", "development_passes", "gate_runs"),
    [
        pytest.param(
            "test ! -f broken.flag || test -f gate-ok",
            None,
            2,
            2,
            id="red-then-fixed-on-the-next-development-pass",
        ),
        pytest.param(
            'if [ "$(grep -c . RUNS)" -eq 1 ]; then kill -TERM $$; fi; '
            "echo '[DEADLINE REACHED] budget of 5s exceeded'; exit 3",
            "inconclusive",
            1,
            MAX_INCONCLUSIVE_GATE_RUNS,
            id="inconclusive-signal-kill-then-deadline-marker",
        ),
        pytest.param("no-such-soak-gate --all", "never started", 1, 1, id="invocation-error"),
    ],
)
def test_a_run_through_each_publication_gate_outcome_lands_or_says_why(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gate: str,
    cause: str | None,
    development_passes: int,
    gate_runs: int,
) -> None:
    runs = tmp_path / "gate-runs.log"
    soak = SoakRun(
        tmp_path,
        monkeypatch,
        gate=f"`echo run >> {runs}; {gate.replace('RUNS', str(runs))}`",
        hooks={("development_commit", 1): _break_the_target},
    )
    soak.seed()
    seeded_target = soak.target_sha()

    outcome = soak.run()

    assert_soak_invariant(soak, outcome, unpublished_cause=cause)
    assert outcome.phases.count("development") == development_passes, outcome.describe()
    assert runs.read_text(encoding="utf-8").splitlines() == ["run"] * gate_runs
    if cause is None:
        assert git(soak.root, "rev-parse", "HEAD") == soak.target_sha()
    else:
        assert git(soak.primary, "rev-list", "--count", f"{seeded_target}..{TARGET}") == "1"


def test_a_misconfigured_gate_declaration_completes_with_the_commits_held_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    soak = SoakRun(tmp_path, monkeypatch, gate="run the tests (see the wiki")
    soak.seed()
    seeded_target = soak.target_sha()

    outcome = soak.run()

    assert_soak_invariant(soak, outcome, unpublished_cause="misconfigured")
    assert outcome.phases.count("development") == 1, outcome.describe()
    assert soak.target_sha() == seeded_target
