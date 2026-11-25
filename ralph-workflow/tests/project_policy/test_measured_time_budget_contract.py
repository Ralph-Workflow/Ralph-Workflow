from __future__ import annotations

import pytest

from ralph.project_policy import markers, preflight, starters, validators
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus, stack

_VERIFICATION = "verification-policy.md"
_TESTING = "testing-policy.md"


def _ready_workspace() -> MemoryWorkspace:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    return ws


def _policy_path(filename: str) -> str:
    return f"{markers.CANONICAL_DIR}{filename}"


def _append(ws: MemoryWorkspace, filename: str, text: str) -> None:
    path = _policy_path(filename)
    ws.write(path, ws.read(path) + "\n" + text + "\n")


def _ids(ws: MemoryWorkspace) -> list[str]:
    return [f.requirement_id for f in validators.validate_readiness(ws, stack())]


def _time_budget_ids(ws: MemoryWorkspace) -> list[str]:
    return [rid for rid in _ids(ws) if rid.startswith(markers.ID_TIME_BUDGET)]


def test_complete_corpus_satisfies_the_measured_budget_contract() -> None:
    assert _ids(_ready_workspace()) == []


@pytest.mark.parametrize("fact_key", ["verification_cost_floor", "fast_path_time_budget"])
def test_verification_policy_without_a_measured_budget_fact_is_not_ready(fact_key: str) -> None:
    ws = _ready_workspace()
    path = _policy_path(_VERIFICATION)
    kept = [
        line
        for line in ws.read(path).splitlines()
        if not line.startswith(f"{markers.FACT_MARKER} {fact_key}:")
    ]
    ws.write(path, "\n".join(kept))
    assert f"{markers.ID_PLACEHOLDER}:{_VERIFICATION}:fact-{fact_key}" in _ids(ws)


@pytest.mark.parametrize(
    ("filename", "legacy_text"),
    [
        (
            _VERIFICATION,
            "Target 10 seconds for the fast\n    path, HARD CAP 30 seconds. Startup alone exceeding the target is a\n"
            "    defect to fix, not a reason to widen it.",
        ),
        (
            _VERIFICATION,
            "   - **HARD CAP: 2 minutes**, whatever the size — a gate slower than that",
        ),
        (
            _VERIFICATION,
            "RALPH-NOTE: full gate hard cap 2 minutes and fast path hard cap 30 seconds"
            " until the project records tighter figures.",
        ),
        (
            _TESTING,
            "   whole-gate guide, leaving the remainder for other steps. HARD CAP 60\n   seconds.",
        ),
        (_TESTING, "The default suite has a **HARD CAP** 60 seconds."),
        (
            _VERIFICATION,
            "Startup alone exceeding the target is a\n    defect to fix; record the floor.",
        ),
    ],
)
def test_fixed_universal_time_cap_wording_blocks_readiness(filename: str, legacy_text: str) -> None:
    ws = _ready_workspace()
    _append(ws, filename, legacy_text)
    assert _time_budget_ids(ws) == [f"{markers.ID_TIME_BUDGET}:{filename}:fixed-cap"]


def test_fixed_cap_wording_in_a_frozen_policy_still_blocks_readiness() -> None:
    ws = _ready_workspace()
    path = _policy_path(_VERIFICATION)
    frozen = ws.read(path).replace(
        markers.POLICY_SCHEMA_MARKER, "<!-- ralph-policy-schema: freeze v5 -->"
    )
    ws.write(path, frozen + "\nTarget 10 seconds for the fast path, HARD CAP 30 seconds.\n")
    assert _time_budget_ids(ws) == [f"{markers.ID_TIME_BUDGET}:{_VERIFICATION}:fixed-cap"]


def test_fixed_cap_finding_names_the_replacement_contract() -> None:
    ws = _ready_workspace()
    _append(ws, _VERIFICATION, "path, HARD CAP 30 seconds.")
    finding = next(
        f
        for f in validators.validate_readiness(ws, stack())
        if f.requirement_id.startswith(markers.ID_TIME_BUDGET)
    )
    assert finding.blocking is True
    assert "HARD CAP 30 seconds" in finding.missing_evidence
    assert "verification_cost_floor" in finding.required_outcome
    assert "fast_path_time_budget" in finding.required_outcome


@pytest.mark.parametrize(
    "allowed_text",
    [
        "Target 10 seconds above the floor, HARD CAP 30 seconds above the recorded cost floor.",
        "The 75-second combined budget is a **HARD CAP**, not a target to grow into.",
        "per-step timeouts sit at 30 s in ralph/verify.py:_VERIFY_STEP_TIMEOUT_SECONDS",
    ],
)
def test_floor_relative_caps_and_project_figures_are_allowed(allowed_text: str) -> None:
    ws = _ready_workspace()
    _append(ws, _VERIFICATION, allowed_text)
    _append(ws, _TESTING, allowed_text)
    assert _time_budget_ids(ws) == []


def test_fixed_cap_wording_outside_the_budget_policies_is_ignored() -> None:
    ws = _ready_workspace()
    _append(ws, "linting-policy.md", "Lint runs must finish; HARD CAP 30 seconds.")
    assert _time_budget_ids(ws) == []


def test_bundled_starters_ship_no_fixed_universal_time_cap() -> None:
    ws = MemoryWorkspace()
    preflight.run_policy_readiness_preflight(ws, stack())
    for filename in (_VERIFICATION, _TESTING):
        assert starters.read_starter(filename) == ws.read(_policy_path(filename))
    assert _time_budget_ids(ws) == []


def test_preflight_sends_a_ready_project_with_fixed_caps_to_remediation() -> None:
    ws = _ready_workspace()
    assert not preflight.run_policy_readiness_preflight(ws, stack()).requires_remediation()
    _append(ws, _VERIFICATION, "Target 10 seconds for the fast path, HARD CAP 30 seconds.")
    result = preflight.run_policy_readiness_preflight(ws, stack())
    assert result.requires_remediation()
    assert f"{markers.ID_TIME_BUDGET}:{_VERIFICATION}:fixed-cap" in [
        f.requirement_id for f in result.findings
    ]
