from __future__ import annotations

from ralph.recovery.attempt_facts import (
    TerminationIssuer,
    WorkspaceChangeState,
    workspace_changed_state,
)
from tests.recovery._corpus_derive import (
    ALLOWED_ATTRIBUTIONS,
    ALLOWED_LABELS,
    ALLOWED_VERDICTS,
    CorpusCase,
    derive_attempt_facts,
    derive_workspace_change,
    load_corpus,
    load_fixtures,
)

EXPECTED_POSITIVE_IDS = frozenset({"agy_quota_error_step"})
EXPECTED_NEGATIVE_IDS = frozenset(
    {
        "claude_live_session_end_no_completion",
        "codex_quote_kill",
        "incident_7_claude_probe_trust_dialog_rc1",
        "incident_7_claude_stop_hook_pending_subagents",
        "opencode_post_exit_watchdog_descendant_hang",
        "opencode_subagent_kill",
        "transient_reconnecting_events",
        "slow_productive_midrun_error",
        "wrote_then_failed",
        "outcome_already_exists",
        "peers_fail_identically",
    }
)
REQUIRED_FIXTURE_FIELDS = (
    "id",
    "label",
    "agent",
    "transport",
    "captured_lines",
    "expected_attribution",
    "expected_bench",
    "expected_verdict",
    "tool_calls",
    "workspace_changed",
    "workspace_changed_by_tool",
    "artifact_valid",
    "elapsed_to_terminal_ms",
    "exit_status",
    "liveness_observed",
    "subagent_activity_observed",
    "termination",
)


def _case(case_id: str) -> CorpusCase:
    cases = {case.id: case for case in load_corpus()}
    assert case_id in cases, f"the corpus does not hold a case named {case_id}"
    return cases[case_id]


def test_every_fixture_loads_and_carries_the_required_fields() -> None:
    fixtures = load_fixtures()

    assert fixtures, "the corpus directory holds no fixture"
    for fixture in fixtures:
        missing = [field for field in REQUIRED_FIXTURE_FIELDS if field not in fixture]
        assert not missing, f"fixture {fixture.get('id')!r} is missing {missing}"


def test_every_fixture_label_is_from_the_allowed_set() -> None:
    for fixture in load_fixtures():
        assert fixture["label"] in ALLOWED_LABELS
        assert fixture["expected_attribution"] in ALLOWED_ATTRIBUTIONS
        assert fixture["expected_verdict"] in ALLOWED_VERDICTS


def test_the_corpus_holds_the_exact_positive_and_negative_sets() -> None:
    cases = load_corpus()

    assert all(isinstance(case, CorpusCase) for case in cases)
    positives = {case.id for case in cases if case.label == "positive"}
    negatives = {case.id for case in cases if case.label == "negative"}

    assert positives == EXPECTED_POSITIVE_IDS
    assert negatives == EXPECTED_NEGATIVE_IDS
    assert len(cases) == len(positives) + len(negatives)


def test_the_corpus_report_line_counts_the_labelled_cases() -> None:
    cases = load_corpus()
    positives = sum(1 for case in cases if case.label == "positive")
    negatives = sum(1 for case in cases if case.label == "negative")

    report = f"fixtures={len(cases)}, positives={positives}, negatives={negatives}"

    assert report == "fixtures=12, positives=1, negatives=11"


def test_the_agy_case_derives_one_event_and_no_tool_calls() -> None:
    facts = _case("agy_quota_error_step").facts

    assert facts.event_kinds == ("output_line",)
    assert facts.tool_calls == ()
    assert facts.workspace_changed is False
    assert facts.artifact_valid is False
    assert facts.liveness_observed is False


def test_the_codex_case_derives_the_read_only_mcp_tool_call() -> None:
    facts = _case("codex_quote_kill").facts

    assert facts.event_kinds == ("output_line", "output_line", "output_line", "output_line")
    assert facts.tool_calls == ("read_multiple_files",)
    assert facts.termination is not None
    assert facts.termination.issuer is TerminationIssuer.QUOTA_DETECTOR


def test_the_unknown_workspace_change_survives_the_derivation() -> None:
    facts = _case("opencode_subagent_kill").facts

    assert facts.workspace_changed is None
    assert workspace_changed_state(facts) is WorkspaceChangeState.UNKNOWN
    assert derive_workspace_change({"workspace_changed": "unknown"}) is None
    assert derive_workspace_change({"workspace_changed": "false"}) is False
    assert derive_workspace_change({"workspace_changed": "true"}) is True


def test_the_derivation_is_pure_for_a_repeated_fixture() -> None:
    fixture = next(item for item in load_fixtures() if item["id"] == "wrote_then_failed")

    first = derive_attempt_facts(fixture)
    second = derive_attempt_facts(fixture)

    assert first == second
    assert first.tool_calls == ("write",)
