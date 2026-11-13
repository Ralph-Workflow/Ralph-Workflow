from __future__ import annotations

from types import MappingProxyType

import pytest

from ralph.testing import repo_structure_boundary_rules as boundary_rules
from ralph.testing.repo_structure_boundary_rules import (
    B4_ALLOWLIST,
    RULE_PAIRINGS,
    allowlist_justification,
    collect_boundary_violations,
    rule_pairing,
)

_CONTROLLER_PATH = "ralph/recovery/controller.py"

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "real controller source read replaced with an inline snapshot": (
        "the test policy audit bans repository .read_text() in tests; the real "
        "recovery controller's _spent_agents source is inlined as a snapshot so "
        "the B2 boundary check keeps proving the sanctioned controller "
        "operations trigger no violations without filesystem I/O"
    ),
}

_REAL_CONTROLLER_SPENT_AGENTS_SNAPSHOT = """
class RecoveryController:
    def __init__(self, opts):
        self._spent_agents: dict[str, set[str]] = {
            k: set(v) for k, v in (opts.spent_agents or {}).items()
        }

    def _is_agent_spent(self, phase: str, agent: str) -> bool:
        if agent in self._spent_agents.get(phase, ()):
            return True
        return isinstance(self._ledger.status(phase, agent), Spent)

    def reset_backoff(self, phase: str, agent: str | None) -> None:
        self._spent_agents.pop(phase, None)

    def note_retry_exhaustion(self, phase: str, agent: str) -> None:
        self._spent_agents.setdefault(phase, set()).add(agent)

    def preferred_agent_index(self, phase: str, agents) -> None:
        spent_set = self._spent_agents.get(phase, set())

    def snapshot(self) -> dict[str, object]:
        return {
            "spent_agents": {k: sorted(v) for k, v in self._spent_agents.items()},
        }

    def _enter_phase_failed(self, state) -> None:
        self._spent_agents.pop(state.phase, None)
"""


@pytest.mark.parametrize(
    ("rule", "path", "violating", "clean"),
    [
        ("B1", "ralph/cli/run.py", "RecoveryController()", "OtherController()"),
        (
            "B2",
            "ralph/pipeline/state.py",
            "value = current_allowance_spent",
            "value = allowance_spent",
        ),
        (
            "B3",
            "ralph/pipeline/state.py",
            "tracker.mark_unavailable(agent)",
            "tracker.mark_available(agent)",
        ),
        ("B4", "ralph/cli/run.py", "os.kill(pid, signal.SIGTERM)", "os.kill(pid, 0)"),
        (
            "B5",
            "ralph/cli/run.py",
            "from ralph.agents.invoke import invoke_agent",
            "def run(invoke_agent):\n    return invoke_agent()",
        ),
        (
            "B6",
            "ralph/recovery/end_attempt.py",
            "if 'quota' in stderr:\n    pass",
            "if facts.quota_exhausted:\n    pass",
        ),
        (
            "B7",
            "ralph/recovery/end_attempt.py",
            "CACHE = {}",
            "CACHE: Final = frozenset()",
        ),
        (
            "B8",
            "ralph/agents/invoke/_quota_detection.py",
            "from ralph.process import ManagedProcess",
            "from ralph.agents.parsers import content_channel",
        ),
        (
            "B8",
            "ralph/agents/parsers/content_channel.py",
            "from ralph.process import supervisor",
            "from dataclasses import dataclass",
        ),
    ],
)
def test_boundary_rule_rejects_violation_and_accepts_clean_source(
    rule: str, path: str, violating: str, clean: str
) -> None:
    assert any(f" {rule} " in item for item in collect_boundary_violations(path, violating))
    assert not any(f" {rule} " in item for item in collect_boundary_violations(path, clean))


def test_b2_rejects_unsanctioned_controller_spent_mutation() -> None:
    source = "self._spent_agents.setdefault(phase, set()).add(agent)"
    violations = collect_boundary_violations("ralph/recovery/controller.py", source)
    assert any(" B2 " in item for item in violations)


def test_b2_accepts_sanctioned_controller_spent_operations() -> None:
    source = """
def note_retry_exhaustion(self, phase, agent):
    self._spent_agents.setdefault(phase, set()).add(agent)

def reset(self, phase):
    self._spent_agents.pop(phase, None)
"""
    assert not any(
        " B2 " in item
        for item in collect_boundary_violations("ralph/recovery/controller.py", source)
    )


def test_b5_rejects_module_alias_attribute_access() -> None:
    source = """
import ralph.pipeline.effect_executor as effects
effects.execute_agent_effect()
"""
    violations = collect_boundary_violations("ralph/cli/run.py", source)
    assert any(" B5 " in item for item in violations)


def test_testing_tree_is_skipped() -> None:
    assert collect_boundary_violations("ralph/testing/example.py", "RecoveryController()") == []


def test_b4_skips_the_whole_testing_tree_without_an_allowlist_entry() -> None:
    source = "os.killpg(pgid, signal.SIGKILL)"
    assert collect_boundary_violations("ralph/testing/pytest_timeout_plugin.py", source) == []


def test_b2_accepts_controller_spent_reads() -> None:
    source = """
def is_spent(self, phase, agent):
    if agent in self._spent_agents.get(phase, ()):
        return True
    return bool(self._spent_agents.items())
"""
    assert not any(" B2 " in item for item in collect_boundary_violations(_CONTROLLER_PATH, source))


def test_b2_accepts_controller_constructor_assignment() -> None:
    source = """
class RecoveryController:
    def __init__(self, opts):
        self._spent_agents: dict[str, set[str]] = {
            k: set(v) for k, v in (opts.spent_agents or {}).items()
        }
"""
    assert not any(" B2 " in item for item in collect_boundary_violations(_CONTROLLER_PATH, source))


def test_b2_accepts_controller_sanctioned_write() -> None:
    source = """
def note_retry_exhaustion(self, phase, agent):
    self._spent_agents.setdefault(phase, set()).add(agent)
"""
    assert not any(" B2 " in item for item in collect_boundary_violations(_CONTROLLER_PATH, source))


def test_b2_accepts_controller_pop_reset() -> None:
    source = """
def reset_backoff(self, phase):
    self._spent_agents.pop(phase, None)
"""
    assert not any(" B2 " in item for item in collect_boundary_violations(_CONTROLLER_PATH, source))


def test_b2_rejects_controller_subscript_write() -> None:
    source = """
def seed(self, phase):
    self._spent_agents[phase] = set()
"""
    assert any(" B2 " in item for item in collect_boundary_violations(_CONTROLLER_PATH, source))


def test_b2_rejects_controller_other_mutation() -> None:
    source = """
def seed(self, agent):
    self._spent_agents.add(agent)
"""
    assert any(" B2 " in item for item in collect_boundary_violations(_CONTROLLER_PATH, source))


def test_b2_rejects_controller_assignment_outside_the_constructor() -> None:
    source = """
def reseed(self):
    self._spent_agents = {}
"""
    assert any(" B2 " in item for item in collect_boundary_violations(_CONTROLLER_PATH, source))


def test_b2_rejects_spent_agents_reference_outside_the_controller() -> None:
    source = "value = controller._spent_agents.get(phase, ())"
    assert any(
        " B2 " in item for item in collect_boundary_violations("ralph/pipeline/state.py", source)
    )


def test_real_recovery_controller_spent_operations_have_no_b2_violations() -> None:
    assert not any(
        " B2 " in item
        for item in collect_boundary_violations(
            _CONTROLLER_PATH, _REAL_CONTROLLER_SPENT_AGENTS_SNAPSHOT
        )
    )


def test_b7_flags_mutable_module_level_state_and_accepts_frozen_forms() -> None:
    flagged = collect_boundary_violations("ralph/recovery/end_attempt.py", "__all__ = ['name']")
    assert any(" B7 " in item for item in flagged)
    tuple_form = collect_boundary_violations("ralph/recovery/end_attempt.py", "__all__ = ('name',)")
    assert not any(" B7 " in item for item in tuple_form)
    proxy_form = collect_boundary_violations(
        "ralph/recovery/end_attempt.py", "__all__ = MappingProxyType({'name': 1})"
    )
    assert not any(" B7 " in item for item in proxy_form)


def test_rule_pairings_cover_every_boundary_rule() -> None:
    assert {rule for rule, _pairing in RULE_PAIRINGS} == {
        "B1",
        "B2",
        "B3",
        "B4",
        "B5",
        "B6",
        "B7",
        "B8",
    }
    assert rule_pairing("B2") == "AC16"
    assert rule_pairing("B8") == "AC4"


@pytest.mark.parametrize(
    "mutation",
    [
        "self._spent_agents.add(agent)",
        "self._spent_agents.update({'phase': {'agent'}})",
        "self._spent_agents.clear()",
        "self._spent_agents.popitem()",
        "self._spent_agents.remove(agent)",
        "self._spent_agents.discard(agent)",
        "self._spent_agents.setdefault(phase, set())",
        "self._spent_agents[phase].add(agent)",
        "self._spent_agents[phase].clear()",
    ],
)
def test_b2_rejects_every_unsanctioned_controller_mutation(mutation: str) -> None:
    source = f"""
def seed(self, phase, agent):
    {mutation}
"""
    assert any(" B2 " in item for item in collect_boundary_violations(_CONTROLLER_PATH, source))


@pytest.mark.parametrize(
    "reset",
    [
        "self._spent_agents.pop(phase, None)",
        "self._spent_agents.pop(phase)",
        "self._spent_agents[phase].pop()",
    ],
)
def test_b2_accepts_every_controller_pop_reset(reset: str) -> None:
    source = f"""
def reset_backoff(self, phase):
    {reset}
"""
    assert not any(" B2 " in item for item in collect_boundary_violations(_CONTROLLER_PATH, source))


@pytest.mark.parametrize(
    "read",
    [
        "self._spent_agents.get(phase, ())",
        "self._spent_agents.get(phase, set())",
        "self._spent_agents.items()",
        "self._spent_agents.keys()",
        "self._spent_agents.values()",
        "phase in self._spent_agents",
        "len(self._spent_agents)",
        "for _phase, _agents in self._spent_agents.items():\n        pass",
    ],
)
def test_b2_accepts_every_controller_spent_read(read: str) -> None:
    source = f"""
def is_spent(self, phase, agent):
    {read}
"""
    assert not any(" B2 " in item for item in collect_boundary_violations(_CONTROLLER_PATH, source))


def test_b2_accepts_the_ledger_spent_status_construction() -> None:
    source = "status=Spent(reason='exhausted')"
    assert not any(
        " B2 " in item
        for item in collect_boundary_violations("ralph/recovery/agent_failure_ledger.py", source)
    )


def test_b2_rejects_spent_status_construction_outside_the_ledger() -> None:
    source = "status=Spent(reason='exhausted')"
    assert any(
        " B2 " in item for item in collect_boundary_violations("ralph/pipeline/state.py", source)
    )


def test_b2_rejects_allowance_spent_reads_outside_the_ledger() -> None:
    source = "value = opts.current_allowance_spent"
    assert any(
        " B2 " in item for item in collect_boundary_violations("ralph/pipeline/state.py", source)
    )


def test_b4_allowlist_entry_with_justification_suppresses_the_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        boundary_rules,
        "B4_ALLOWLIST",
        MappingProxyType({"ralph/pipeline/legacy.py": "reviewed exception"}),
    )
    source = "os.killpg(pgid, signal.SIGKILL)"
    assert collect_boundary_violations("ralph/pipeline/legacy.py", source) == []


def test_b4_allowlist_entry_without_justification_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        boundary_rules,
        "B4_ALLOWLIST",
        MappingProxyType({"ralph/pipeline/legacy.py": "   "}),
    )
    with pytest.raises(ValueError, match="B4 allowlist entry without justification"):
        collect_boundary_violations("ralph/pipeline/legacy.py", "os.killpg(pgid, 1)")


def test_b4_allowlist_carries_no_redundant_testing_tree_entry() -> None:
    assert "ralph/testing/pytest_timeout_plugin.py" not in B4_ALLOWLIST
    assert allowlist_justification("B4", "ralph/testing/pytest_timeout_plugin.py") is None


def test_allowlist_justification_is_absent_for_unlisted_paths() -> None:
    assert allowlist_justification("B4", "ralph/pipeline/state.py") is None
    assert allowlist_justification("B5", "ralph/pipeline/state.py") is None


def test_b7_flags_a_final_annotated_mutable_literal() -> None:
    flagged = collect_boundary_violations("ralph/recovery/end_attempt.py", "CACHE: Final = {}")
    assert any(" B7 " in item for item in flagged)


@pytest.mark.parametrize(
    "frozen",
    [
        "CACHE: Final = frozenset({'name'})",
        "CACHE = frozenset({'name'})",
        "CACHE = ('name',)",
        "CACHE = MappingProxyType({'name': 1})",
    ],
)
def test_b7_accepts_frozen_module_level_state(frozen: str) -> None:
    assert not any(
        " B7 " in item
        for item in collect_boundary_violations("ralph/recovery/end_attempt.py", frozen)
    )


def test_decision_layer_rules_apply_only_to_the_named_modules() -> None:
    flagged = collect_boundary_violations("ralph/pipeline/agent_retry_decision.py", "import re")
    assert any(" B6 " in item for item in flagged)
    unflagged = collect_boundary_violations("ralph/pipeline/state.py", "import re")
    assert not any(" B6 " in item for item in unflagged)


def test_b6_flags_exception_text_conversion_in_the_decision_layer() -> None:
    flagged = collect_boundary_violations(
        "ralph/pipeline/retryable_failure.py", "message = str(exc)"
    )
    assert any(" B6 " in item for item in flagged)


def test_rule_module_has_no_module_docstring() -> None:
    assert boundary_rules.__doc__ is None


@pytest.mark.parametrize(
    "path",
    [
        "ralph/agents/invoke/_quota_detection.py",
        "ralph/agents/parsers/content_channel.py",
    ],
)
def test_b8_rejects_process_imports_and_managed_process_references(path: str) -> None:
    assert any(
        " B8 " in item for item in collect_boundary_violations(path, "import ralph.process")
    )
    assert any(
        " B8 " in item
        for item in collect_boundary_violations(path, "import ralph.process.supervisor as sup")
    )
    assert any(
        " B8 " in item
        for item in collect_boundary_violations(path, "from ralph.process.supervisor import sup")
    )
    assert any(
        " B8 " in item
        for item in collect_boundary_violations(path, "def f(proc):\n    return proc.ManagedProcess")
    )
    assert not any(
        " B8 " in item for item in collect_boundary_violations(path, "from ralph.agents import invoke")
    )


@pytest.mark.parametrize(
    "source",
    ["import ralph.process", "ManagedProcess()", "from ralph.process import supervisor"],
)
def test_b8_leaves_non_detector_paths_untouched(source: str) -> None:
    assert collect_boundary_violations("ralph/cli/run.py", source) == []


def test_b8_missing_detector_file_is_no_violation() -> None:
    assert not any(
        " B8 " in item
        for item in collect_boundary_violations(
            "ralph/agents/parsers/content_channel.py", "from dataclasses import dataclass"
        )
    )
