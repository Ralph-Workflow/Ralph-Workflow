from __future__ import annotations

from types import MappingProxyType

import pytest

from ralph.testing import repo_structure_boundary_rules as boundary_rules
from ralph.testing.repo_structure_boundary_rules import (
    B4_ALLOWLIST,
    B11_ALLOWLIST,
    RULE_PAIRINGS,
    allowlist_justification,
    collect_boundary_violations,
    rule_pairing,
)

_CONTROLLER_PATH = "ralph/recovery/controller.py"

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
        "B9",
        "B9b",
        "B10",
        "B11",
    }
    assert rule_pairing("B2") == "AC16"
    assert rule_pairing("B11") == "AC-matrix-completeness"
    assert rule_pairing("B8") == "AC4"
    assert rule_pairing("B9") == "AC3/AC15"
    assert rule_pairing("B9b") == "AC3/AC15"
    assert rule_pairing("B10") == "AC13"


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
    assert any(" B8 " in item for item in collect_boundary_violations(path, "import ralph.process"))
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
        for item in collect_boundary_violations(
            path, "def f(proc):\n    return proc.ManagedProcess"
        )
    )
    assert not any(
        " B8 " in item
        for item in collect_boundary_violations(path, "from ralph.agents import invoke")
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


_B9_INVOKE_SOURCE = """
def f(handle, td, pid, issuer):
    handle.terminate(issuer=issuer)
    teardown_subtree(pid, issuer=issuer)
    td.teardown_subtree(pid, issuer=issuer)
"""


@pytest.mark.parametrize(
    "path",
    [
        "ralph/agents/invoke/_completion_supervision.py",
        "ralph/agents/idle_watchdog/_detector.py",
    ],
)
def test_b9_rejects_signalling_calls_in_invoke_layer_files(path: str) -> None:
    b9_hits = [
        item for item in collect_boundary_violations(path, _B9_INVOKE_SOURCE) if " B9 " in item
    ]
    assert len(b9_hits) == 3


@pytest.mark.parametrize(
    "source",
    [
        "handle.terminate(issuer=issuer)",
        "teardown_subtree(pid, issuer=issuer)",
        "td.teardown_subtree(pid, issuer=issuer)",
    ],
)
def test_b9_accepts_signalling_calls_in_the_attempt_supervisor(source: str) -> None:
    path = "ralph/agents/invoke/attempt_supervision/attempt_supervisor.py"
    assert not any(" B9 " in item for item in collect_boundary_violations(path, source))


def test_b9_accepts_teardown_subtree_passed_as_a_reference() -> None:
    source = "supervisor.act(observation, teardown=teardown_subtree)"
    assert not any(
        " B9 " in item
        for item in collect_boundary_violations("ralph/agents/invoke/_pty_line_reader.py", source)
    )


_B9B_INVOKE_PATHS = [
    "ralph/agents/invoke/_pty_line_reader.py",
    "ralph/agents/idle_watchdog/_post_exit_watchdog.py",
]


@pytest.mark.parametrize("path", _B9B_INVOKE_PATHS)
def test_b9b_rejects_runtime_teardown_subtree_imports_and_references(path: str) -> None:
    for source in (
        "from ralph.process.teardown import teardown_subtree",
        "import ralph.process.teardown",
        "teardown_subtree(pid, issuer=issuer)",
    ):
        assert any(" B9b " in item for item in collect_boundary_violations(path, source))


def test_b9b_flags_the_reference_b9_accepts() -> None:
    source = "supervisor.act(observation, teardown=teardown_subtree)"
    assert any(
        " B9b " in item
        for item in collect_boundary_violations("ralph/agents/invoke/_pty_line_reader.py", source)
    )


def test_b9b_accepts_teardown_subtree_passed_as_a_parameter() -> None:
    source = (
        "def run(supervisor, observation, teardown_subtree):\n"
        "    supervisor.act(observation, teardown=teardown_subtree)"
    )
    assert not any(
        " B9b " in item
        for item in collect_boundary_violations("ralph/agents/invoke/_pty_line_reader.py", source)
    )


def test_b9b_accepts_type_checking_only_process_teardown_imports() -> None:
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from ralph.process.teardown import ProcessTeardown\n"
    )
    assert not any(
        " B9b " in item
        for item in collect_boundary_violations("ralph/agents/invoke/_pty_line_reader.py", source)
    )


@pytest.mark.parametrize(
    "source",
    [
        "process_teardown.teardown_subtree(pid, issuer=issuer)",
        "some_obj.teardown_subtree",
    ],
)
def test_b9b_accepts_injected_seam_attribute_access(source: str) -> None:
    assert not any(
        " B9b " in item
        for item in collect_boundary_violations("ralph/agents/invoke/_pty_line_reader.py", source)
    )


@pytest.mark.parametrize(
    "path",
    [
        "ralph/agents/invoke/attempt_supervision/teardown_fn.py",
        "ralph/agents/invoke/attempt_supervision/attempt_supervisor.py",
        "ralph/process/teardown.py",
        "ralph/agents/subprocess_executor.py",
    ],
)
def test_b9b_leaves_out_of_scope_and_supervision_paths_untouched(path: str) -> None:
    source = (
        "from ralph.process.teardown import teardown_subtree\n"
        "def run(pid):\n"
        "    teardown_subtree(pid, issuer=issuer)"
    )
    assert not any(" B9b " in item for item in collect_boundary_violations(path, source))


def test_b9b_pairs_with_matrix_ac3_ac15() -> None:
    assert rule_pairing("B9b") == "AC3/AC15"


@pytest.mark.parametrize("call", ["proc.terminate()", "proc.kill()"])
def test_b9_rejects_untyped_termination_outside_the_process_layer(call: str) -> None:
    assert any(
        " B9 " in item
        for item in collect_boundary_violations("ralph/mcp/protocol/transport.py", call)
    )


@pytest.mark.parametrize("call", ["handle.terminate(issuer=issuer)", "handle.kill(issuer=issuer)"])
def test_b9_rejects_typed_termination_outside_the_process_layer(call: str) -> None:
    assert any(
        " B9 " in item
        for item in collect_boundary_violations("ralph/mcp/protocol/transport.py", call)
    )


@pytest.mark.parametrize(
    "source",
    [
        "get_process_manager().terminate(proc, issuer=issuer)",
        "get_process_manager().terminate(\n    proc,\n    grace_period_s=5.0,\n    issuer=issuer,\n    reason='mcp_shutdown',\n)",
        "get_process_manager().kill(proc, issuer=issuer)",
        "pm = get_process_manager()\npm.terminate(handle, issuer=issuer)",
        "pm = _pm if _pm is not None else get_process_manager()\npm.terminate(handle, issuer=issuer)",
        "_SHARD_MANAGER = ProcessManager(policy=policy)\n_SHARD_MANAGER.terminate(proc, issuer=issuer)",
        "self._pm = (\n    process_manager if process_manager is not None else get_process_manager()\n)\nself._pm.terminate(handle, grace_period_s=0.0, issuer=issuer)",
        "terminate_unmanaged(process, grace_period_s=0.0, issuer=issuer)",
        "kill_unmanaged(process, issuer=issuer)",
    ],
)
def test_b9_accepts_manager_routed_termination_outside_the_process_layer(source: str) -> None:
    assert not any(
        " B9 " in item
        for item in collect_boundary_violations("ralph/mcp/protocol/transport.py", source)
    )


def test_b9_accepts_manager_bound_local_shadowing() -> None:
    source = """
def run(handle, pm):
    pm = get_process_manager()
    pm.terminate(handle, issuer=issuer)
"""
    assert not any(
        " B9 " in item for item in collect_boundary_violations("ralph/executor/process.py", source)
    )


@pytest.mark.parametrize(
    "source",
    [
        'setattr(exc, "attempt_facts", facts)',
        'getattr(exc, "attempt_facts", None)',
        "attach_attempt_facts(exc, facts)",
        "value = attempt_facts_from_exception(exc)",
        "from ralph.recovery.attempt_facts import attempt_facts_from_exception",
    ],
)
def test_b10_rejects_dynamic_transport_of_attempt_facts(source: str) -> None:
    assert any(
        " B10 " in item
        for item in collect_boundary_violations("ralph/pipeline/attempt_runner_deps.py", source)
    )


@pytest.mark.parametrize(
    "source",
    [
        "setattr(exc, 'attempt_facts_path', path)",
        "getattr(exc, 'termination_record')",
        "publish_attempt_facts(exc, recorder)",
        "facts = attempt_facts_for_failure(exc)",
        "from ralph.recovery.attempt_facts import AttemptFacts",
    ],
)
def test_b10_accepts_typed_attempt_facts_access(source: str) -> None:
    assert not any(
        " B10 " in item
        for item in collect_boundary_violations("ralph/pipeline/attempt_runner_deps.py", source)
    )


def test_b10_skips_the_process_tree() -> None:
    source = 'setattr(record, "attempt_facts", facts)'
    assert not any(
        " B10 " in item
        for item in collect_boundary_violations("ralph/process/manager/_process_manager.py", source)
    )


def _b11_nested_dispatch_source(function_name: str, call_line: int) -> str:
    return (
        "\n" * (call_line - 3)
        + f"def {function_name}(target: AgentConfig):\n"
        + "    def dispatch():\n"
        + "        return invoke_agent(target)\n"
    )


def test_b11_regression_rejects_agent_dispatch_outside_allowlist() -> None:
    source = "def dispatch(target: AgentConfig):\n    return invoke_agent(target)\n"
    violations = collect_boundary_violations("ralph/pipeline/unregistered.py", source)
    assert any(
        " B11 agent dispatch outside the entry-point registry allowlist" in item
        for item in violations
    )


@pytest.mark.parametrize(
    ("path", "function_name", "call_line", "_justification"),
    B11_ALLOWLIST,
)
def test_b11_regression_accepts_allowlisted_agent_dispatch(
    path: str,
    function_name: str,
    call_line: int,
    _justification: str,
) -> None:
    source = _b11_nested_dispatch_source(function_name, call_line)
    violations = collect_boundary_violations(path, source)
    assert not any(" B11 " in item for item in violations)


def test_b9_leaves_module_level_signalling_to_b4() -> None:
    assert not any(
        " B9 " in item
        for item in collect_boundary_violations("ralph/git/_worktree_lease.py", "os.kill(pid, 0)")
    )
    assert not any(
        " B9 " in item
        for item in collect_boundary_violations(
            "ralph/interrupt/dispatcher.py", "os.killpg(pgid, signal.SIGKILL)"
        )
    )
    assert not any(
        " B9 " in item
        for item in collect_boundary_violations(
            "ralph/interrupt/dispatcher.py", "os.kill(record.pid, signal.SIGKILL)"
        )
    )


@pytest.mark.parametrize(
    "path",
    [
        "ralph/process/manager/_managed_process.py",
        "ralph/testing/pytest_timeout_plugin.py",
    ],
)
def test_b9_skips_the_process_and_testing_trees(path: str) -> None:
    assert not any(" B9 " in item for item in collect_boundary_violations(path, "proc.terminate()"))


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "RALPH_TEST_CHANGE_REASONS b8 assertion lines reflowed without semantic change": (
        "ruff format reflowed the assert generator expressions in "
        "test_b8_rejects_process_imports_and_managed_process_references; "
        "the checked conditions, violation strings, and expected outcomes "
        "are unchanged"
    ),
    "real controller source read replaced with an inline snapshot": (
        "the test policy audit bans repository .read_text() in tests; the real "
        "recovery controller's _spent_agents source is inlined as a snapshot so "
        "the B2 boundary check keeps proving the sanctioned controller "
        "operations trigger no violations without filesystem I/O"
    ),
    "test_b8_rejects_process_imports_and_managed_process_references": "ruff format rewrap only",
}
