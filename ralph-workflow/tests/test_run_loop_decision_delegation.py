"""Static scan: the reducer is the single ``last_decision`` writer.

Contract: ``run_loop.py`` and ``runner.py`` must not write
``RebaseState.last_decision`` directly (assignment or ``model_copy(update=...)``).
The reducer (``ralph/pipeline/reducer.py``) is the only writer; both modules
must instead consult the typed authorities: the single integration owner
(:func:`ralph.pipeline.integration_owner.decide_integration`, reached through
:func:`ralph.pipeline._integration_owner_seam.decide_seam_action`) and
``decide_phase_transition``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TARGETS = [
    _REPO_ROOT / "ralph" / "pipeline" / "run_loop.py",
    _REPO_ROOT / "ralph" / "pipeline" / "runner.py",
]


def _last_decision_writer_lines(path: Path) -> list[str]:
    """Offending ``last_decision`` writer sites in ``path``."""
    offenders: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return offenders
    for node in ast.walk(tree):
        # model_copy(update={"last_decision": ...}) or copy_with(last_decision=...)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"copy_with", "model_copy"}
        ):
            window = "\n".join(lines[node.lineno - 1 : node.end_lineno])
            if "last_decision=" in window:
                offenders.append(f"{path.name}:{node.lineno}")
            for key in _dict_str_keys(node):
                if key == "last_decision":
                    offenders.append(f"{path.name}:{node.lineno}")
    return offenders


def _dict_str_keys(call: ast.Call) -> list[str]:
    keys: list[str] = []
    for kw in call.keywords:
        if kw.arg == "update" and isinstance(kw.value, ast.Dict):
            keys.extend(str(k.value) for k in kw.value.keys if isinstance(k, ast.Constant))
    return keys


def test_no_direct_last_decision_writers() -> None:
    offenders = [line for path in _TARGETS for line in _last_decision_writer_lines(path)]
    assert offenders == []


def test_authority_calls_present() -> None:
    run_loop = _TARGETS[0].read_text(encoding="utf-8")
    runner = _TARGETS[1].read_text(encoding="utf-8")
    assert "decide_seam_action(" in run_loop
    assert "decide_phase_transition(" in run_loop
    assert "decide_seam_action(" in runner
    assert "decide_phase_transition(" in runner
    assert "from ralph.pipeline._integration_owner_seam import" in run_loop
    assert "from ralph.pipeline._integration_owner_seam import" in runner
    assert "from ralph.pipeline._pipeline_state_machine import" in run_loop
    assert "from ralph.pipeline._pipeline_state_machine import" in runner


def test_the_retired_per_seam_decider_is_gone() -> None:
    """The single owner replaced the hand-rolled per-seam decider.

    ``next_integration_action`` re-derived the integration rung from a
    durable record inside each seam. The owner
    (:mod:`ralph.pipeline.integration_owner`) now decides the mutation
    ladder and the seams overlay the durable record facts, so no seam
    may call the retired decider any more.
    """
    for path in _TARGETS:
        source = path.read_text(encoding="utf-8")
        assert "next_integration_action(" not in source, (
            f"{path.name} must ask the single owner, not the retired per-seam decider"
        )


def test_the_run_loop_has_no_hand_rolled_block_decider() -> None:
    """The run loop's blocking seam must not keep a private record decider.

    ``_resolve_record_for_block`` decided the busy / unreadable record
    outcome with a private table. The canonical record-fact helper
    (``busy_refusal_outcome``) owns that verdict now.
    """
    run_loop = _TARGETS[0].read_text(encoding="utf-8")
    assert "_resolve_record_for_block" not in run_loop
    assert "busy_refusal_outcome" in run_loop


def test_run_loop_no_prose_compare_on_last_decision() -> None:
    """Invariant 7 / 13: ``last_decision`` may not be compared against a string literal.

    The reducer is the sole ``last_decision`` writer; the run loop
    must compare typed ``PhaseDecision`` enum values via
    ``.value`` rather than the literal string, so a future change to
    the enum cannot silently disagree with the run loop helper.
    """
    run_loop = _TARGETS[0].read_text(encoding="utf-8")
    # The fix: replace the literal "report_intervention_required" with
    # ``PhaseDecision.REPORT_INTERVENTION_REQUIRED.value``. The literal
    # must not survive anywhere in run_loop.py outside this comment.
    assert 'last_decision == "report_intervention_required"' not in run_loop
    assert "PhaseDecision.REPORT_INTERVENTION_REQUIRED.value" in run_loop


def test_integration_alert_for_state_uses_typed_phase_decision() -> None:
    """The integration alert helper must trip on the typed PhaseDecision value.

    Both a typed ``PhaseDecision`` and a string ``last_decision``
    round-trip through ``_integration_alert_for_state``; the helper
    may not disagree with the reducer's typed answer.
    """
    from ralph.pipeline import run_loop
    from ralph.pipeline._pipeline_state_machine import PhaseDecision
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.pipeline.state import PipelineState

    rebase_typed = RebaseState(last_decision=PhaseDecision.REPORT_INTERVENTION_REQUIRED.value)
    rebase_string = RebaseState(
        last_decision="report_intervention_required"  # StrEnum round-trip
    )
    # WU-1: the alert names the next autonomous ladder rung and the
    # preserved copy's location — never a person.
    expected = (
        "autonomous fallback ladder escalation — preserved copy: backup refs; "
        "next autonomous rung: preserve the work, then climb the cleanup ladder"
    )
    state_typed = PipelineState(phase="development", rebase=rebase_typed)
    state_string = PipelineState(phase="development", rebase=rebase_string)
    assert run_loop._integration_alert_for_state(state_typed) == expected
    assert run_loop._integration_alert_for_state(state_string) == expected
    assert "manual recovery" not in expected

    rebase_evidence = RebaseState(
        last_outcome=__import__(
            "ralph.pipeline._rebase_outcome_kind", fromlist=["RebaseOutcomeKind"]
        ).RebaseOutcomeKind.DIAGNOSING
    )
    state_evidence = PipelineState(phase="development", rebase=rebase_evidence)
    assert run_loop._integration_alert_for_state(state_evidence) == (
        "evidence diagnosis — gathering operation facts"
    )
