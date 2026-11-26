from __future__ import annotations

import re

from ralph.project_policy import markers, starters


def _flat(name: str) -> str:
    return re.sub(r"\s+", " ", starters.read_starter(name))


def test_verification_gate_fails_only_past_the_cap_never_the_measurement() -> None:
    text = _flat("verification-policy.md")
    assert "The gate FAILS only when its controllable time exceeds the cap below" in text
    assert "this cap is the limit the gate enforces" in text
    assert "not a limit the gate fails on" in text
    assert "with no headroom" not in text
    assert "fail on exceed" not in text
    assert "MUST fail when it exceeds that budget" not in text


def test_fast_path_fails_only_past_its_cap() -> None:
    text = _flat("verification-policy.md")
    assert "The fast path FAILS only when it exceeds that cap" in text
    assert "held to 10 and 30 seconds outright" not in text


def test_contended_overrun_gets_exactly_one_rerun_never_a_wait_for_a_quiet_host() -> None:
    verification = _flat("verification-policy.md")
    testing = _flat("testing-policy.md")
    assert "gets exactly ONE re-run" in verification
    assert "NEVER re-run repeatedly waiting for a quiet host" in verification
    assert "Never re-run repeatedly waiting for a quiet host" in testing


def test_suite_runner_limit_is_the_cap_not_the_recorded_measurement() -> None:
    text = _flat("testing-policy.md")
    assert "The runner-enforced limit of rule 8 is the suite's HARD CAP below" in text
    assert "with no headroom" not in text
    assert "crosses 80% of the current budget" not in text


def test_projects_on_the_old_wording_are_offered_the_upgrade() -> None:
    assert markers.SCHEMA_VERSION == "v7"
    for name in starters.iter_starter_names():
        assert starters.read_starter(name).startswith(markers.POLICY_SCHEMA_MARKER), name
