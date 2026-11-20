from __future__ import annotations

from pathlib import Path

from ralph.testing import audit_integration_invariants
from ralph.testing.audit_integration_no_legacy import audit_modules

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "restored_gated_e6e382c62_version": (
        "4959fb143 added checks for a conflict-recovery redesign that is reverted; the file returns"
        " to the gated e6e382c62 version, which pins the restored behaviour"
    ),
    "test_planted_operator_parking_literals_fail_no_legacy_audit": (
        "pins the no-legacy audit rule family that rejects NeedsOperator, "
        "operator action required, LandedProofFailed, and a LadderState parking member"
    ),
    "test_planted_missing_gitlink_policy_owner_is_detected": (
        "pins the gitlink_policy_owner_missing rule; without it the pointer-preference "
        "owner module could disappear from the tree unnoticed"
    ),
    "test_planted_incomplete_gitlink_policy_owner_is_detected": (
        "pins the gitlink_policy_owner_incomplete rule; the owner module must define "
        "both the candidate list and the chooser"
    ),
    "test_planted_missing_gitlink_resolution_module_is_detected": (
        "pins the gitlink_resolution_module_missing rule; the in-place resolver module must exist"
    ),
    "test_planted_gitlink_conflict_not_resolved_in_place_is_detected": (
        "pins the gitlink_not_resolved_in_place rule; the resolution module must route "
        "the conflict through the pointer policy owner"
    ),
    "test_planted_agent_eligible_gitlink_set_is_detected": (
        "pins the gitlink conflict classified agent-eligible rule for an agent-eligible "
        "set that holds a gitlink member"
    ),
    "test_planted_agent_eligible_gitlink_comparison_is_detected": (
        "pins the gitlink conflict classified agent-eligible rule for a comparison "
        "between a gitlink member and an agent-eligible set"
    ),
    "test_planted_watchdog_reason_class_without_operator_cap_is_detected": (
        "pins the operator_cap_reason_missing rule for a watchdog reason class that "
        "drops the operator cap member"
    ),
    "test_planted_watchdog_reason_reference_without_operator_cap_is_detected": (
        "pins the operator_cap_reason_missing rule for a module that uses the watchdog "
        "reason class while the class no longer carries the operator cap member"
    ),
    "test_planted_watchdog_reason_owner_without_operator_cap_is_detected": (
        "pins the operator_cap_reason_missing rule for a tree whose watchdog reason "
        "owner module no longer defines the reason class"
    ),
}

_ENTRY_SOURCE = """from __future__ import annotations

from ralph.pipeline.key_unavailable import KeyUnavailable


def _resolve_rebase_with_config(root, target, resolver, config):
    return None


def compute_resolution_attempt_key(root, record, config=None, **kwargs):
    if False:
        return KeyUnavailable("stub")
    return None
"""

_PIPELINE_INIT_SOURCE = """from __future__ import annotations

__all__: list[str] = []
"""

_PIPELINE_INIT_ALL_SOURCE = """from __future__ import annotations

__all__ = ["resolve_rebase_with_config"]
"""

_PIPELINE_INIT_LAZY_SOURCE = """from __future__ import annotations

_LAZY_EXPORTS = {
    "resolve_rebase_with_config": (
        "ralph.pipeline._stop_resolution_entry",
        "resolve_rebase_with_config",
    ),
}
"""

_OWNER_SOURCE = """from __future__ import annotations

from typing import Final

EXPECTED_EXIT_CODES: Final[dict[str, frozenset[int]]] = {
    "git-is-ancestor": frozenset({1}),
}


def expected_exit_codes(label: str | None) -> frozenset[int]:
    if not label:
        return frozenset()
    return EXPECTED_EXIT_CODES.get(label, frozenset())
"""

_GITLINK_POLICY_SOURCE = """POLICY_CANDIDATES = ()
def choose_gitlink(): pass
"""

_GITLINK_RESOLUTION_SOURCE = """# choose_gitlink
"""

_WATCHDOG_REASON_SOURCE = """class WatchdogFireReason:
    OPERATOR_CAP_REACHED = "operator_cap_reached"
"""

_INCOMPLETE_GITLINK_POLICY_SOURCE = """POLICY_CANDIDATES = ()
"""

_GITLINK_CONFLICT_SOURCE = """class GitlinkConflict:
    GITLINK_POINTER = "gitlink"
"""

_GITLINK_ELIGIBLE_SET_SOURCE = """from ralph.pipeline.conflict_resolution.gitlink_conflict import GitlinkConflict


AGENT_ELIGIBLE_KINDS = frozenset({GitlinkConflict.GITLINK_POINTER})
"""

_GITLINK_ELIGIBLE_COMPARE_SOURCE = """from ralph.pipeline.conflict_resolution.gitlink_conflict import GitlinkConflict


AGENT_ELIGIBLE_KINDS = frozenset()


def is_agent_eligible(kind):
    return GitlinkConflict.GITLINK_POINTER in AGENT_ELIGIBLE_KINDS
"""

_WATCHDOG_REASON_WITHOUT_CAP_SOURCE = """class WatchdogFireReason:
    OTHER_REASON = "other_reason"
"""

_WATCHDOG_REASON_REFERENCE_SOURCE = """from ralph.agents.watchdog_fire_reason import WatchdogFireReason


def pick(reason):
    return reason is WatchdogFireReason
"""


def _write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _remove(root: Path, relative: str) -> None:
    (root / relative).unlink()


def _clean_tree(tmp_path: Path) -> Path:
    _write(tmp_path, "ralph/__init__.py", "")
    _write(
        tmp_path,
        "ralph/agents/watchdog_fire_reason.py",
        "from enum import StrEnum\n\n\n"
        "class WatchdogFireReason(StrEnum):\n"
        '    OPERATOR_CAP_REACHED = "OPERATOR_CAP_REACHED"\n',
    )
    _write(tmp_path, "ralph/pipeline/__init__.py", _PIPELINE_INIT_SOURCE)
    _write(tmp_path, "ralph/pipeline/_stop_resolution_entry.py", _ENTRY_SOURCE)
    _write(
        tmp_path,
        "ralph/pipeline/conflict_resolution/gitlink_policy.py",
        "POLICY_CANDIDATES = ()\n\n\ndef choose_gitlink():\n    return None\n",
    )
    _write(
        tmp_path,
        "ralph/pipeline/conflict_resolution/deterministic_resolution.py",
        "from ralph.pipeline.conflict_resolution.gitlink_policy import choose_gitlink\n",
    )

    _write(tmp_path, "ralph/process/__init__.py", "")
    _write(tmp_path, "ralph/process/manager/__init__.py", "")
    _write(tmp_path, "ralph/process/manager/_process_manager_runtime.py", _OWNER_SOURCE)
    _write(tmp_path, "ralph/pipeline/conflict_resolution/gitlink_policy.py", _GITLINK_POLICY_SOURCE)
    _write(
        tmp_path,
        "ralph/pipeline/conflict_resolution/deterministic_resolution.py",
        _GITLINK_RESOLUTION_SOURCE,
    )
    _write(tmp_path, "ralph/agents/watchdog_fire_reason.py", _WATCHDOG_REASON_SOURCE)
    return tmp_path


def _rules(tree: Path) -> list[str]:
    return [violation.rule for violation in audit_integration_invariants.audit_tree(tree)]


def _locations(tree: Path, rule: str) -> list[str]:
    return [
        f"{violation.path}:{violation.line}"
        for violation in audit_integration_invariants.audit_tree(tree)
        if violation.rule == rule
    ]


def test_clean_fixture_tree_reports_no_violations(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)

    assert _rules(tree) == []


def test_planted_package_reexport_of_the_resolver_is_detected(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _write(tree, "ralph/pipeline/__init__.py", _PIPELINE_INIT_ALL_SOURCE)

    assert "resolver_export_from_package" in _rules(tree)
    assert "ralph/pipeline/__init__.py:3" in _locations(tree, "resolver_export_from_package")


def test_planted_package_lazy_export_of_the_resolver_is_detected(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _write(tree, "ralph/pipeline/__init__.py", _PIPELINE_INIT_LAZY_SOURCE)

    assert "resolver_export_from_package" in _rules(tree)
    assert "ralph/pipeline/__init__.py:4" in _locations(tree, "resolver_export_from_package")


def test_planted_direct_call_to_the_private_resolver_is_detected(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _write(
        tree,
        "ralph/pipeline/caller.py",
        "from ralph.pipeline._stop_resolution_entry import _resolve_rebase_with_config\n"
        "\n"
        "\n"
        "def run(root, target):\n"
        "    return _resolve_rebase_with_config(root, target, None, None)\n",
    )

    assert "resolver_reference_outside_entry" in _rules(tree)
    assert "ralph/pipeline/caller.py:1" in _locations(tree, "resolver_reference_outside_entry")


def test_planted_definition_of_the_private_resolver_is_detected(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _write(
        tree,
        "ralph/pipeline/rival.py",
        "def _resolve_rebase_with_config(root, target, resolver, config):\n    return None\n",
    )

    assert "resolver_defined_outside_entry" in _rules(tree)
    assert "ralph/pipeline/rival.py:1" in _locations(tree, "resolver_defined_outside_entry")


def test_planted_bare_critical_call_in_a_pipeline_module_is_detected(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _write(
        tree,
        "ralph/pipeline/emitter.py",
        'from loguru import logger\n\n\ndef run():\n    logger.critical("boom")\n',
    )

    assert "terminal_emitter_bypass" in _rules(tree)
    assert "ralph/pipeline/emitter.py:5" in _locations(tree, "terminal_emitter_bypass")


def test_planted_competing_exit_code_table_is_detected(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _write(
        tree,
        "ralph/process/manager/_rival_table.py",
        "from typing import Final\n"
        "\n"
        "EXPECTED_EXIT_CODES: Final[dict[str, frozenset[int]]] = {\n"
        '    "git-is-ancestor": frozenset({1}),\n'
        "}\n",
    )

    rules = _rules(tree)

    assert "severity_table_constant_conflict" in rules
    assert "competing_label_exit_code_table" in rules
    assert "ralph/process/manager/_rival_table.py:3" in _locations(
        tree, "competing_label_exit_code_table"
    )


def test_planted_severity_decision_outside_the_owner_is_detected(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _write(
        tree,
        "ralph/git/severity.py",
        "from loguru import logger\n"
        "\n"
        "\n"
        "def decide(result, label):\n"
        '    if result.returncode == 1 and label == "git-is-ancestor":\n'
        '        logger.debug("quiet")\n'
        "    else:\n"
        '        logger.warning("loud")\n',
    )

    assert "severity_decision_outside_owner" in _rules(tree)
    assert "ralph/git/severity.py:5" in _locations(tree, "severity_decision_outside_owner")


def test_label_producer_without_a_severity_branch_is_not_flagged(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _write(
        tree,
        "ralph/git/probe.py",
        "from loguru import logger\n"
        "\n"
        "\n"
        "def run(probe):\n"
        '    probe = run_git(probe, label="git-is-ancestor")\n'
        "    if probe.returncode != 0:\n"
        '        logger.warning("probe failed")\n'
        "    return probe\n",
    )

    assert _rules(tree) == []


def test_planted_violation_makes_main_report_failure(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _write(
        tree,
        "ralph/pipeline/emitter.py",
        'from loguru import logger\n\n\ndef run():\n    logger.critical("boom")\n',
    )

    assert audit_integration_invariants.main([str(tree)]) == 1


def test_clean_tree_makes_main_report_success(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)

    assert audit_integration_invariants.main([str(tree)]) == 0


def test_planted_operator_parking_literals_fail_no_legacy_audit() -> None:
    planted = {
        "ralph/pipeline/planted_needs_operator.py": 'state = "NeedsOperator"\n',
        "ralph/pipeline/planted_operator_action.py": (
            'msg = "operator action required: land the commit"\n'
        ),
        "ralph/pipeline/planted_landed_proof.py": 'state = "LandedProofFailed"\n',
        "ralph/pipeline/planted_ladder_state.py": (
            "from enum import StrEnum\n"
            "class LadderState(StrEnum):\n"
            '    NEEDS_OPERATOR = "NeedsOperator"\n'
        ),
    }
    rules = {v.rule for v in audit_modules(planted, ignore_transitional=True)}
    assert "operator-parking terminal literal reappeared" in rules
    assert "operator-parking LadderState member reappeared" in rules


def test_planted_missing_gitlink_policy_owner_is_detected(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _remove(tree, "ralph/pipeline/conflict_resolution/gitlink_policy.py")

    assert "gitlink_policy_owner_missing" in _rules(tree)
    assert "ralph/pipeline/conflict_resolution/gitlink_policy.py:1" in _locations(
        tree, "gitlink_policy_owner_missing"
    )


def test_planted_incomplete_gitlink_policy_owner_is_detected(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _write(
        tree,
        "ralph/pipeline/conflict_resolution/gitlink_policy.py",
        _INCOMPLETE_GITLINK_POLICY_SOURCE,
    )

    assert "gitlink_policy_owner_incomplete" in _rules(tree)
    assert "ralph/pipeline/conflict_resolution/gitlink_policy.py:1" in _locations(
        tree, "gitlink_policy_owner_incomplete"
    )


def test_planted_missing_gitlink_resolution_module_is_detected(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _remove(tree, "ralph/pipeline/conflict_resolution/deterministic_resolution.py")

    assert "gitlink_resolution_module_missing" in _rules(tree)
    assert "ralph/pipeline/conflict_resolution/deterministic_resolution.py:1" in _locations(
        tree, "gitlink_resolution_module_missing"
    )


def test_planted_gitlink_conflict_not_resolved_in_place_is_detected(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _write(
        tree,
        "ralph/pipeline/conflict_resolution/deterministic_resolution.py",
        "def resolve():\n    return None\n",
    )

    assert "gitlink_not_resolved_in_place" in _rules(tree)
    assert "ralph/pipeline/conflict_resolution/deterministic_resolution.py:1" in _locations(
        tree, "gitlink_not_resolved_in_place"
    )


def test_planted_agent_eligible_gitlink_set_is_detected(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _write(
        tree,
        "ralph/pipeline/conflict_resolution/gitlink_conflict.py",
        _GITLINK_CONFLICT_SOURCE,
    )
    _write(
        tree,
        "ralph/pipeline/conflict_resolution/runner.py",
        _GITLINK_ELIGIBLE_SET_SOURCE,
    )

    assert "gitlink conflict classified agent-eligible" in _rules(tree)
    assert "ralph/pipeline/conflict_resolution/runner.py:4" in _locations(
        tree, "gitlink conflict classified agent-eligible"
    )


def test_planted_agent_eligible_gitlink_comparison_is_detected(tmp_path: Path) -> None:
    tree = _clean_tree(tmp_path)
    _write(
        tree,
        "ralph/pipeline/conflict_resolution/gitlink_conflict.py",
        _GITLINK_CONFLICT_SOURCE,
    )
    _write(
        tree,
        "ralph/pipeline/conflict_resolution/runner.py",
        _GITLINK_ELIGIBLE_COMPARE_SOURCE,
    )

    assert "gitlink conflict classified agent-eligible" in _rules(tree)
    assert "ralph/pipeline/conflict_resolution/runner.py:8" in _locations(
        tree, "gitlink conflict classified agent-eligible"
    )


def test_planted_watchdog_reason_class_without_operator_cap_is_detected(
    tmp_path: Path,
) -> None:
    tree = _clean_tree(tmp_path)
    _write(
        tree,
        "ralph/agents/watchdog_fire_reason.py",
        _WATCHDOG_REASON_WITHOUT_CAP_SOURCE,
    )

    assert "operator_cap_reason_missing" in _rules(tree)
    assert "ralph/agents/watchdog_fire_reason.py:1" in _locations(
        tree, "operator_cap_reason_missing"
    )


def test_planted_watchdog_reason_reference_without_operator_cap_is_detected(
    tmp_path: Path,
) -> None:
    tree = _clean_tree(tmp_path)
    _write(
        tree,
        "ralph/agents/watchdog_fire_reason.py",
        'OPERATOR_CAP_REACHED = "OPERATOR_CAP_REACHED"\n',
    )
    _write(tree, "ralph/agents/reason_user.py", _WATCHDOG_REASON_REFERENCE_SOURCE)

    assert "operator_cap_reason_missing" in _rules(tree)
    assert "ralph/agents/reason_user.py:5" in _locations(tree, "operator_cap_reason_missing")


def test_planted_watchdog_reason_owner_without_operator_cap_is_detected(
    tmp_path: Path,
) -> None:
    tree = _clean_tree(tmp_path)
    _remove(tree, "ralph/agents/watchdog_fire_reason.py")
    _write(tree, "ralph/agents/pacer.py", "def pace():\n    return None\n")

    assert "operator_cap_reason_missing" in _rules(tree)
    assert "ralph/agents/watchdog_fire_reason.py:1" in _locations(
        tree, "operator_cap_reason_missing"
    )
