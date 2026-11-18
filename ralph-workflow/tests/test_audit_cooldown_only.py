from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import ralph.testing.audit_cooldown_only as audit_module
from ralph.testing.audit_cooldown_only import (
    GUARD_TESTS,
    LEDGER_MODULE,
    SELECTION_MODULE,
    TRACKER_MODULE,
    WAIT_KIND_MODULE,
    Violation,
    collect_violations,
    guard_test_violations,
    main,
    source_violations,
)

if TYPE_CHECKING:
    from pathlib import Path

_LEDGER_HEADER = """
from dataclasses import dataclass

class _AgentStatus:
    reason: str = ""

@dataclass(frozen=True)
class _Eligible(_AgentStatus):
    pass
"""

_COOLDOWN = """
@dataclass(frozen=True)
class _Cooldown(_AgentStatus):
    until_ms: int
    reason: str
"""

_ALIAS = "\ntype AgentEligibility = Eligible | CoolingDown\n"


def _details(violations: list[Violation]) -> str:
    return "\n".join(str(violation) for violation in violations)


def test_the_repository_passes_the_cooldown_only_audit() -> None:
    assert collect_violations(only=("recovery/", SELECTION_MODULE)) == []


def test_a_status_class_without_an_expiry_fails_the_audit() -> None:
    source = (
        _LEDGER_HEADER
        + _COOLDOWN
        + """
@dataclass(frozen=True)
class _Spent(_AgentStatus):
    reason: str
"""
        + _ALIAS
    )

    violations = source_violations(source, LEDGER_MODULE)

    assert "_Spent is a third availability state" in _details(violations)


@pytest.mark.parametrize("name", ["_Benched", "_Parked", "_Retired", "_Demoted"])
def test_a_third_status_under_any_name_fails_the_audit(name: str) -> None:
    source = (
        _LEDGER_HEADER
        + _COOLDOWN
        + f"""
@dataclass(frozen=True)
class {name}(_AgentStatus):
    reason: str
"""
        + _ALIAS
    )

    assert f"{name} is a third availability state" in _details(
        source_violations(source, LEDGER_MODULE)
    )


def test_a_cooldown_without_a_required_expiry_fails_the_audit() -> None:
    source = (
        _LEDGER_HEADER
        + """
@dataclass(frozen=True)
class _Cooldown(_AgentStatus):
    until_ms: int | None
    reason: str
"""
        + _ALIAS
    )

    assert "_Cooldown must carry a required `until_ms: int`" in _details(
        source_violations(source, LEDGER_MODULE)
    )


def test_an_eligibility_alias_admitting_a_non_cooldown_state_fails_the_audit() -> None:
    source = _LEDGER_HEADER + _COOLDOWN + "\ntype AgentEligibility = Eligible | Cooldown | Spent\n"

    assert "AgentEligibility admits non-cooldown states: ['Spent']" in _details(
        source_violations(source, LEDGER_MODULE)
    )


def test_a_wait_kind_without_an_expiry_fails_the_audit() -> None:
    source = """
from enum import StrEnum

class WaitKind(StrEnum):
    AVAILABLE_NOW = "available_now"
    COOLING_DOWN = "cooling_down"
    NEVER_ELIGIBLE = "never_eligible"
"""

    assert "NEVER_ELIGIBLE" in _details(source_violations(source, WAIT_KIND_MODULE))


def test_a_tracker_entry_with_an_optional_expiry_fails_the_audit() -> None:
    source = """
from dataclasses import dataclass

@dataclass(frozen=True)
class UnavailabilityEntry:
    unavailable_until_ms: int | None
"""

    assert "unavailable_until_ms must be a required int expiry" in _details(
        source_violations(source, TRACKER_MODULE)
    )


def test_a_selection_row_with_an_extra_exclusion_flag_fails_the_audit() -> None:
    source = """
def agent_availability(agent, available, cooldown_ms_remaining, spent, cooldown_reason=None):
    return agent, available, cooldown_ms_remaining, spent, cooldown_reason
"""

    assert "selection rows may only carry availability and cooldown" in _details(
        source_violations(source, SELECTION_MODULE)
    )


@pytest.mark.parametrize(
    "statement",
    [
        "self._spent_agents = {}",
        "controller.note_retry_exhaustion(phase, agent)",
        "status.is_spent",
        "reason = self._last_demoted_reason",
        "def mark_benched(agent):\n    return agent",
        "ledger.all_exhausted(phase, agents)",
        "ledger.requires_intervention(phase, agents)",
    ],
)
def test_an_exclusion_state_identifier_in_the_engine_fails_the_audit(statement: str) -> None:
    violations = source_violations(statement + "\n", "pipeline/example.py")

    assert "names an agent exclusion state outside the cooldown system" in _details(violations)


def test_writing_the_tracker_outside_its_owners_fails_the_audit() -> None:
    source = "ledger.mark_unavailable(phase, agent, reason='dead')\n"

    assert "only the recovery controller and the failure ledger" in _details(
        source_violations(source, "pipeline/conflict_resolution/session.py")
    )


def test_a_deleted_guard_test_module_fails_the_audit() -> None:
    rel_path, names = next(iter(GUARD_TESTS.items()))

    assert "was deleted" in _details(guard_test_violations(None, rel_path, names))


def test_a_removed_or_skipped_guard_test_fails_the_audit() -> None:
    rel_path, names = next(iter(GUARD_TESTS.items()))
    kept, *removed = names
    source = (
        f"import pytest\n\n@pytest.mark.skip(reason='flaky')\ndef {kept}() -> None:\n    pass\n"
    )

    details = _details(guard_test_violations(source, rel_path, names))

    assert f"{kept} is disabled" in details
    for name in removed:
        assert f"{name} was removed" in details


def test_a_skip_marked_guard_module_fails_the_audit() -> None:
    rel_path, names = next(iter(GUARD_TESTS.items()))
    body = "".join(f"def {name}() -> None:\n    pass\n\n" for name in names)
    source = f"import pytest\n\npytestmark = pytest.mark.skip\n\n{body}"

    assert "guard module is skip-marked" in _details(guard_test_violations(source, rel_path, names))


def test_the_audit_command_fails_on_a_violation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _planted(
        package_root: Path | None = None,
        project_root: Path | None = None,
        *,
        only: tuple[str, ...] = (),
    ) -> list[Violation]:
        del package_root, project_root, only
        return [Violation("recovery/controller.py", 1, "planted")]

    monkeypatch.setattr(audit_module, "collect_violations", _planted)

    assert main([]) == 1
    assert "COOLDOWN-ONLY AUDIT FAILED" in capsys.readouterr().out
