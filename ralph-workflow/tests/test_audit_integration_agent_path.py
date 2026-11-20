from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.testing.audit_integration_agent_path import (
    AGENT_CLASSIFICATION_RULE,
    AGENT_PROCESS_LAUNCH_RULE,
    AGENT_RETRY_DELAY_RULE,
    AGENT_SELECTION_RULE,
    COOLDOWN_WRITE_RULE,
    GUARDED_INTEGRATION_PATH_PREFIXES,
    SHARED_PATH_BRANCH_RULE,
    audit_source,
    audit_tree,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.testing.audit_integration_agent_path import Violation

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARDED_FIXTURE_PATH = "ralph/pipeline/conflict_resolution/_scratch_fixture.py"
INTEGRATION_FIXTURE_PATH = "ralph/pipeline/_integration_scratch_fixture.py"
INTEGRATION_PACKAGE_FIXTURE_PATH = "ralph/pipeline/integration_ladder.py"
PENDING_MERGE_REPAIR_FIXTURE_PATH = "ralph/pipeline/_pending_merge_repair.py"
SHARED_PATH_FIXTURE_PATH = "ralph/pipeline/attempt_runner.py"


def _rules(violations: Sequence[Violation]) -> list[str]:
    return [violation.rule for violation in violations]


def test_repository_tree_reports_no_violation() -> None:
    assert audit_tree(REPO_ROOT) == []


def test_rule_a_agent_failure_classification_is_reported() -> None:
    source = """
from ralph.recovery.failure_classifier import FailureClassifier


def classify_outcome(attempt):
    return FailureClassifier().classify(attempt)
"""
    violations = audit_source(GUARDED_FIXTURE_PATH, source)
    assert _rules(violations) == [AGENT_CLASSIFICATION_RULE, AGENT_CLASSIFICATION_RULE]
    assert all(violation.path == GUARDED_FIXTURE_PATH for violation in violations)
    assert all(violation.line > 0 for violation in violations)


def test_rule_a_resolver_outcome_vocabulary_is_not_agent_failure_classification() -> None:
    source = """
from ralph.pipeline.conflict_resolution.round_verdict import (
    ResolutionTerminationReason,
    classify_resolution_failure,
)


def outcome(round_state):
    return classify_resolution_failure(round_state, ResolutionTerminationReason.ATTEMPT_FAILED)
"""
    assert audit_source(GUARDED_FIXTURE_PATH, source) == []


def test_rule_b_agent_retry_delay_is_reported() -> None:
    source = """
from ralph.pipeline.auto_integrate_backoff import compute_retry_delay


def delay(attempt):
    return compute_retry_delay(attempt)
"""
    violations = audit_source(GUARDED_FIXTURE_PATH, source)
    assert _rules(violations) == [AGENT_RETRY_DELAY_RULE]


def test_rule_b_git_lock_retry_modules_keep_their_own_delays() -> None:
    source = """
from ralph.pipeline.auto_integrate_backoff import compute_retry_delay


def delay(attempt):
    return compute_retry_delay(attempt)
"""
    assert audit_source("ralph/pipeline/auto_integrate_backoff.py", source) == []
    assert audit_source("ralph/pipeline/auto_integrate_conflict_budget.py", source) == []


def test_rule_c_agent_cooldown_write_is_reported() -> None:
    source = """
def clear_resolver(controller, agent):
    controller.reset_backoff("resolution", agent)
"""
    violations = audit_source(GUARDED_FIXTURE_PATH, source)
    assert _rules(violations) == [COOLDOWN_WRITE_RULE]
    assert violations[0].line == 3


def test_rule_c_shared_finalize_attempt_is_the_permitted_cooldown_write() -> None:
    source = """
def finalize(controller, agent, facts):
    controller.finalize_attempt("resolution", agent, facts=facts)
"""
    assert audit_source(GUARDED_FIXTURE_PATH, source) == []


def test_rule_c_public_cooldown_write_is_reported() -> None:
    source = """
def clear_resolver(controller, agent):
    controller.mark_agent_unavailable("resolution", agent)
"""
    violations = audit_source(GUARDED_FIXTURE_PATH, source)
    assert _rules(violations) == [COOLDOWN_WRITE_RULE]
    assert violations[0].line == 3


def test_rule_c_confirmed_unavailability_write_is_reported() -> None:
    source = """
def clear_resolver(controller, agent):
    controller.record_confirmed_unavailability("resolution", agent, reason)
"""
    violations = audit_source(GUARDED_FIXTURE_PATH, source)
    assert _rules(violations) == [COOLDOWN_WRITE_RULE]


def test_rule_c_integration_package_is_guarded_against_cooldown_writes() -> None:
    source = """
def clear_resolver(controller, agent):
    controller.mark_agent_unavailable("resolution", agent)
"""
    violations = audit_source(INTEGRATION_PACKAGE_FIXTURE_PATH, source)
    assert _rules(violations) == [COOLDOWN_WRITE_RULE]


def test_rule_c_pending_merge_repair_module_is_guarded_against_cooldown_writes() -> None:
    source = """
def clear_resolver(controller, agent):
    controller.mark_agent_unavailable("resolution", agent)
"""
    violations = audit_source(PENDING_MERGE_REPAIR_FIXTURE_PATH, source)
    assert _rules(violations) == [COOLDOWN_WRITE_RULE]


def test_guarded_integration_path_prefixes_cover_the_pending_merge_repair_module() -> None:
    assert "ralph/pipeline/_pending_merge_repair" in GUARDED_INTEGRATION_PATH_PREFIXES


def test_guarded_integration_path_prefixes_cover_the_integration_package() -> None:
    assert "ralph/pipeline/integration" in GUARDED_INTEGRATION_PATH_PREFIXES


def test_rule_d_agent_process_launch_is_reported() -> None:
    source = (
        "import subprocess\n\n\ndef launch(command):\n    return subprocess." + "Popen(command)\n"
    )
    violations = audit_source(INTEGRATION_FIXTURE_PATH, source)
    assert _rules(violations) == [AGENT_PROCESS_LAUNCH_RULE]


def test_rule_d_subprocess_error_in_an_except_tuple_is_not_a_launch() -> None:
    source = """
import subprocess


def run(command):
    try:
        return command()
    except (OSError, subprocess.SubprocessError):
        return None
"""
    assert audit_source(INTEGRATION_FIXTURE_PATH, source) == []


def test_rule_e_shared_path_integration_branch_is_reported() -> None:
    source = """
def dispatch(phase):
    if phase == "conflict_resolution":
        return "resolver"
    return "agent"
"""
    violations = audit_source(SHARED_PATH_FIXTURE_PATH, source)
    assert _rules(violations) == [SHARED_PATH_BRANCH_RULE]
    assert violations[0].line == 3


def test_rule_e_entry_point_configuration_lookup_is_allowed() -> None:
    source = """
def config_for(configs):
    if has_entry_point(configs, "conflict_resolution"):
        return configs["conflict_resolution"]
    return configs["main_phase_loop"]
"""
    assert audit_source(SHARED_PATH_FIXTURE_PATH, source) == []


def test_rule_f_integration_owned_eligibility_helper_is_reported() -> None:
    source = """
def agent_eligible(agents: list[str]) -> bool:
    return bool(agents)
"""
    violations = audit_source(GUARDED_FIXTURE_PATH, source)
    assert _rules(violations) == [AGENT_SELECTION_RULE]
    assert violations[0].line == 2


def test_rule_f_conflict_path_eligibility_is_not_agent_eligibility() -> None:
    source = """
def agent_eligible_paths(kinds: dict[str, str]) -> tuple[str, ...]:
    return tuple(kinds)
"""
    assert audit_source(GUARDED_FIXTURE_PATH, source) == []


def test_rule_f_shared_eligibility_reads_stay_outside_the_guarded_packages() -> None:
    source = """
def wait_for_eligible_agent(controller, candidates):
    return controller.earliest_available_wait_ms("resolution", list(candidates))
"""
    assert audit_source(GUARDED_FIXTURE_PATH, source) == []


def test_audit_tree_reports_a_scratch_tree_violation(tmp_path: Path) -> None:
    scratch = tmp_path / "ralph" / "pipeline" / "conflict_resolution"
    scratch.mkdir(parents=True)
    (scratch / "_scratch_fixture.py").write_text(
        "def clear_resolver(controller):\n    controller.reset_backoff('resolution', 'agent')\n",
        encoding="utf-8",
    )
    violations = audit_tree(tmp_path)
    assert _rules(violations) == [COOLDOWN_WRITE_RULE]
    assert violations[0].path.endswith("ralph/pipeline/conflict_resolution/_scratch_fixture.py")
