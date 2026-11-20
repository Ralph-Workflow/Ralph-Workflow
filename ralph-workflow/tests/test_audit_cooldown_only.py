from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import ralph.testing.audit_cooldown_only as audit_module
from ralph.testing.audit_cooldown_only import (
    GUARD_TESTS,
    LEDGER_MODULE,
    SELECTION_MODULE,
    SOLE_COOLDOWN_DOC,
    TERMINOLOGY_ALIASES,
    TERMINOLOGY_HEADING,
    TRACKER_MODULE,
    WAIT_KIND_MODULE,
    Violation,
    agents_md_violations,
    doc_heading_violations,
    doc_phrase_violations,
    documentation_violations,
    guard_test_module_violations,
    guard_test_violations,
    main,
    module_test_name_violations,
    recovery_source_paths,
    sole_doc_violations,
    source_violations,
    source_violations_for_paths,
    tree_test_name_violations,
)

if TYPE_CHECKING:
    from pathlib import Path

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_old_agent_availability_phrasing_in_a_doc_fails_the_audit": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_agent_availability_bench_or_spent_phrasing_in_a_doc_fails_the_audit with an unchanged body"
    ),
    "test_the_repository_passes_the_cooldown_only_audit": (
        "replaces the whole-tree recovery-module assertion with a chunked sweep over recovery_source_paths(); operator rule: every agent exclusion is an expiring cooldown, so no recovery module may name a third availability state"
    ),
    "test_the_repository_test_names_pass_the_cooldown_only_audit": (
        "replaces the whole-tree assertion tree_test_name_violations() == [] with a chunked sweep over test_name_scan_paths(); operator rule: every agent exclusion is an expiring cooldown, so no test module or test name may use bench/spent/demot/exhaust vocabulary for agent availability"
    ),
}

_RECOVERY_CHUNK_SIZE = 8
_TEST_NAME_CHUNK_SIZE = 200

_RECOVERY_SOURCE_PATHS = recovery_source_paths()
_RECOVERY_CHUNKS = tuple(
    _RECOVERY_SOURCE_PATHS[start : start + _RECOVERY_CHUNK_SIZE]
    for start in range(0, len(_RECOVERY_SOURCE_PATHS), _RECOVERY_CHUNK_SIZE)
) or ((),)

_TEST_NAME_SCAN_PATHS = audit_module.test_name_scan_paths()
_TEST_NAME_CHUNKS = tuple(
    _TEST_NAME_SCAN_PATHS[start : start + _TEST_NAME_CHUNK_SIZE]
    for start in range(0, len(_TEST_NAME_SCAN_PATHS), _TEST_NAME_CHUNK_SIZE)
) or ((),)


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


@pytest.mark.parametrize("chunk", _RECOVERY_CHUNKS)
def test_the_repository_passes_the_cooldown_only_audit(chunk: tuple[str, ...]) -> None:
    assert source_violations_for_paths(chunk) == []


def test_the_recovery_audit_chunks_cover_every_recovery_module() -> None:
    union = tuple(rel_path for chunk in _RECOVERY_CHUNKS for rel_path in chunk)

    assert sorted(union) == sorted(recovery_source_paths())


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


_POINTER = f'COOLDOWN_SYSTEM_DOC = "{SOLE_COOLDOWN_DOC}"\n'


def _sole_doc(aliases: tuple[str, ...] = TERMINOLOGY_ALIASES, *, two_states: bool = True) -> str:
    states = "An agent has exactly two states." if two_states else "An agent has states."
    rows = "".join(f"| {alias} | the agent is on cooldown |\n" for alias in aliases)
    return f"# Agent cooldown\n\n{TERMINOLOGY_HEADING}\n\n{states}\n\n{rows}\n## The invariant\n"


def test_the_repository_documentation_passes_the_cooldown_only_audit() -> None:
    assert documentation_violations() == []


@pytest.mark.parametrize(
    ("rel_path", "statement"),
    [
        ("recovery/attempt_outcome.py", "def benches_agent(self):\n    return True"),
        (
            "recovery/dead_agent_verdict.py",
            "def verdict_benches_agent(verdict):\n    return verdict",
        ),
        ("recovery/controller.py", "bench_applied = outcome.starts_cooldown"),
        ("recovery/controller.py", "feedback.note_bench(phase, agent)"),
        ("recovery/false_cooldown_feedback.py", "class FalseBenchFeedback:\n    pass"),
        ("recovery/controller.py", "agent_is_spent = True"),
        ("pipeline/conflict_resolution/driver.py", "demoted = ['dev']"),
        ("pipeline/reducer.py", "def _handle_chain_exhausted(state):\n    return state"),
        (
            "pipeline/_stop_resolution_entry.py",
            "def _handle_spent_session(session):\n    return session",
        ),
        ("agents/agent_chain.py", "chain.is_exhausted"),
    ],
)
def test_old_agent_vocabulary_in_an_identifier_fails_the_audit(
    rel_path: str, statement: str
) -> None:
    violations = source_violations(statement + "\n", rel_path)

    assert "uses bench/spent/demot/exhaust vocabulary for agent availability" in _details(
        violations
    )


@pytest.mark.parametrize(
    ("rel_path", "literal"),
    [
        ("pipeline/_integration_status.py", "all resolver agents are spent/unavailable"),
        ("pipeline/conflict_resolution/round_verdict.py", "CANDIDATE_EXHAUSTED: no agents"),
        ("pipeline/run_time_report.py", "- [SG-4] False benches: 0."),
        ("recovery/controller.py", "agent benched for the phase"),
        ("recovery/ledger_example.py", "agent demoted to the failed route"),
        ("pipeline/reducer.py", "Agent chain exhausted in phase"),
    ],
)
def test_old_agent_vocabulary_in_a_string_fails_the_audit(rel_path: str, literal: str) -> None:
    violations = source_violations(f"message = {literal!r}\n", rel_path)

    assert "describe it as a cooldown" in _details(violations)


@pytest.mark.parametrize(
    "statement",
    [
        "reason = 'quota exhausted'",
        "session.rounds_spent += 1",
        "if budget_exhausted():\n    pass",
        "state.resolution_exhausted = True",
        "error = 'PiContextExhaustedExitError'",
    ],
)
def test_an_allowlisted_unrelated_meaning_passes_the_vocabulary_audit(statement: str) -> None:
    violations = source_violations(statement + "\n", "recovery/example.py")

    assert "vocabulary for agent availability" not in _details(violations)


def test_the_legacy_checkpoint_keys_pass_only_in_the_migration_module() -> None:
    statement = "legacy = payload.pop('false_bench_feedback')\n"

    assert "vocabulary" not in _details(
        source_violations(statement, "recovery/legacy_guard_migration.py")
    )
    assert "describe it as a cooldown" in _details(
        source_violations(statement, "recovery/controller.py")
    )


def test_the_old_vocabulary_outside_the_agent_scopes_is_not_flagged() -> None:
    source = "message = 'diagnostic repair budget spent; analysis exhausted'\n"

    assert source_violations(source, "pipeline/_integration_diagnostic.py") == []


@pytest.mark.parametrize("module", [TRACKER_MODULE, LEDGER_MODULE])
def test_a_cooldown_owner_without_the_sole_doc_pointer_fails_the_audit(module: str) -> None:
    source = 'COOLDOWN_SYSTEM_DOC = "docs/agents/benching.md"\n'

    assert "must name the sole cooldown-system doc" in _details(source_violations(source, module))


def test_a_cooldown_owner_with_the_sole_doc_pointer_passes_the_pointer_rule() -> None:
    violations = source_violations(_POINTER + "mark_unavailable = None\n", TRACKER_MODULE)

    assert "must name the sole cooldown-system doc" not in _details(violations)


def test_a_missing_sole_cooldown_doc_fails_the_audit() -> None:
    assert "the sole cooldown-system document is missing" in _details(sole_doc_violations(None))


def test_a_sole_doc_without_its_terminology_section_fails_the_audit() -> None:
    text = "# Agent cooldown\n\n## The invariant\n\nAn agent has exactly two states.\n"

    assert "lacks" in _details(sole_doc_violations(text))


@pytest.mark.parametrize("alias", TERMINOLOGY_ALIASES)
def test_a_terminology_section_missing_an_alias_fails_the_audit(alias: str) -> None:
    aliases = tuple(name for name in TERMINOLOGY_ALIASES if name != alias)
    if alias == "bench":
        aliases = tuple(name for name in aliases if name != "dead-agent bench")

    assert f"does not map the alias {alias!r}" in _details(sole_doc_violations(_sole_doc(aliases)))


def test_a_terminology_section_without_the_two_states_fails_the_audit() -> None:
    assert "exactly two states" in _details(sole_doc_violations(_sole_doc(two_states=False)))


def test_a_complete_sole_doc_passes_the_audit() -> None:
    assert sole_doc_violations(_sole_doc()) == []


@pytest.mark.parametrize(
    "heading",
    [
        "## Agent cooldown rules",
        "### Exponential cooldown growth",
        "## Demotion of failing agents",
        "### Benched agents",
        "## Agent availability",
        "### Clearing backoff",
    ],
)
def test_another_doc_defining_cooldown_rules_without_the_link_fails_the_audit(
    heading: str,
) -> None:
    text = f"# Recovery\n\n{heading}\n\nAn agent cools down for 60 seconds.\n\n## Next\n"

    assert "without linking to the sole cooldown-system document" in _details(
        doc_heading_violations(text, "docs/sphinx/recovery.md")
    )


def test_a_cooldown_section_that_links_to_the_sole_doc_passes_the_audit() -> None:
    text = (
        "# Recovery\n\n## Cooldown rules\n\nSee "
        "[the sole cooldown system](../agents/cooldown-invariant.md).\n"
    )

    assert doc_heading_violations(text, "docs/sphinx/recovery.md") == []


@pytest.mark.parametrize(
    "sentence",
    [
        "A benched agent is skipped.",
        "The agent is spent after nine attempts.",
        "Selection skips the demoted agent.",
        "Agent demotion moves it to the failed route.",
        "All resolver agents are spent/unavailable.",
        "The run-time report counts false benches.",
        "If an agent exhausts its retries, Ralph moves on.",
        "The resolver chain is exhausted.",
    ],
)
def test_old_agent_availability_phrasing_in_a_doc_fails_the_audit(
    sentence: str,
) -> None:
    assert "describes agent availability outside the cooldown system" in _details(
        doc_phrase_violations(f"# Guide\n\n{sentence}\n", "docs/agents/guide.md")
    )


def test_the_sole_doc_terminology_section_may_state_the_ban() -> None:
    text = _sole_doc() + "\nA benched agent is skipped.\n"
    in_section = _sole_doc().replace("| bench |", "| a benched agent |")

    assert doc_phrase_violations(in_section, SOLE_COOLDOWN_DOC) == []
    assert doc_phrase_violations(text, SOLE_COOLDOWN_DOC) != []


def test_an_agents_md_that_stops_linking_the_sole_doc_fails_the_audit() -> None:
    text = "### AGENT AVAILABILITY IS COOLDOWN-ONLY\n\nAn agent is on cooldown or available.\n"

    assert "AGENTS.md must name the sole cooldown-system document" in _details(
        agents_md_violations(text, "AGENTS.md")
    )
    assert agents_md_violations(f"See `{SOLE_COOLDOWN_DOC}`.\n", "AGENTS.md") == []


def test_a_documentation_tree_without_the_sole_doc_fails_the_audit(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "AGENTS.md").write_text("No link here.\n", encoding="utf-8")

    details = _details(documentation_violations(tmp_path))

    assert "the sole cooldown-system document is missing" in details
    assert "AGENTS.md must name the sole cooldown-system document" in details


@pytest.mark.parametrize("chunk", _TEST_NAME_CHUNKS)
def test_the_repository_test_names_pass_the_cooldown_only_audit(chunk: tuple[Path, ...]) -> None:
    assert tree_test_name_violations(paths=chunk) == []


def test_the_test_name_chunks_cover_every_test_module() -> None:
    union = tuple(path for chunk in _TEST_NAME_CHUNKS for path in chunk)

    assert sorted(union) == sorted(audit_module.test_name_scan_paths())


def test_the_guard_test_modules_pass_the_cooldown_only_audit() -> None:
    assert guard_test_module_violations() == []


@pytest.mark.parametrize(
    "rel_path",
    [
        "tests/recovery/test_dead_agent_bench_routes.py",
        "tests/recovery/test_false_bench_feedback.py",
        "tests/test_conflict_resolution_rebase_spent_chain.py",
        "tests/recovery/test_ledger_owns_agent_demotion.py",
        "tests/recovery/test_chain_exhaustion.py",
    ],
)
def test_a_test_module_named_with_the_old_vocabulary_fails_the_audit(rel_path: str) -> None:
    assert "test module" in _details(module_test_name_violations("", rel_path))


@pytest.mark.parametrize(
    "name",
    [
        "test_a_failed_probe_benches_the_agent",
        "test_the_agent_is_never_spent",
        "test_unproductive_resolver_is_demoted",
        "test_exhausted_resolver_chain_reports_evidence",
    ],
)
def test_a_test_function_named_with_the_old_vocabulary_fails_the_audit(name: str) -> None:
    source = f"def {name}() -> None:\n    pass\n"

    assert f"test {name!r}" in _details(
        module_test_name_violations(source, "tests/recovery/test_sample.py")
    )


@pytest.mark.parametrize(
    ("rel_path", "name"),
    [
        ("tests/test_explore_bench.py", "test_bench_reports_latency"),
        ("tests/test_sample.py", "test_legacy_spent_snapshot_fields_restore_as_eligible"),
        ("tests/test_sample.py", "test_iterator_exhaustion_flushes_pending_text"),
        ("tests/test_sample.py", "test_the_run_stops_once_the_hang_cap_is_spent"),
        ("tests/test_sample.py", "test_quota_exhausted_starts_out_of_credits"),
    ],
)
def test_an_allowlisted_test_name_passes_the_audit(rel_path: str, name: str) -> None:
    source = f"def {name}() -> None:\n    pass\n"

    assert module_test_name_violations(source, rel_path) == []
