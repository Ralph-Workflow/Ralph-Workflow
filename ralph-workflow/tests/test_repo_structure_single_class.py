from __future__ import annotations

import ast
import importlib
import inspect

import pytest

_AUDITED_MODULES: tuple[tuple[str, str | None], ...] = (
    ("ralph.pipeline.auto_integrate_recovery_terminal", "ResolutionTerminalMarker"),
    ("ralph.pipeline.integration_ladder", None),
    ("ralph.pipeline.ladder_effect", "LadderEffect"),
    ("ralph.pipeline.ladder_effect_kind", "LadderEffectKind"),
    ("ralph.pipeline.ladder_facts", "LadderFacts"),
    ("ralph.pipeline.ladder_state", "LadderState"),
    ("ralph.pipeline.ladder_step_result", "LadderStepResult"),
    ("ralph.pipeline.ladder_transition", "LadderTransition"),
    ("ralph.pipeline.landed_verdict", "LandedVerdict"),
    ("ralph.pipeline.last_resort_agent_pool", "LastResortAgentPool"),
    ("ralph.pipeline.last_resort_agent_proof", "LastResortAgentProof"),
    ("ralph.pipeline.last_resort_wait_proof", "LastResortWaitProof"),
    ("ralph.pipeline.terminal_state_violation_error", "TerminalStateViolationError"),
    ("ralph.recovery.agent_failure_ledger", "AgentFailureLedger"),
    ("ralph.recovery.eligibility_wait", "EligibilityWait"),
    ("ralph.recovery.wait_kind", "WaitKind"),
)

_REEXPORTED_CLASSES: tuple[tuple[str, str, str], ...] = (
    (
        "ralph.pipeline.auto_integrate_recovery_terminal",
        "TerminalStateViolationError",
        "ralph.pipeline.terminal_state_violation_error",
    ),
    ("ralph.pipeline.integration_ladder", "LadderEffect", "ralph.pipeline.ladder_effect"),
    ("ralph.pipeline.integration_ladder", "LadderEffectKind", "ralph.pipeline.ladder_effect_kind"),
    ("ralph.pipeline.integration_ladder", "LadderFacts", "ralph.pipeline.ladder_facts"),
    ("ralph.pipeline.integration_ladder", "LadderState", "ralph.pipeline.ladder_state"),
    ("ralph.pipeline.integration_ladder", "LadderStepResult", "ralph.pipeline.ladder_step_result"),
    ("ralph.pipeline.integration_ladder", "LadderTransition", "ralph.pipeline.ladder_transition"),
    ("ralph.pipeline.integration_ladder", "LandedVerdict", "ralph.pipeline.landed_verdict"),
    (
        "ralph.pipeline.integration_ladder",
        "LastResortAgentPool",
        "ralph.pipeline.last_resort_agent_pool",
    ),
    (
        "ralph.pipeline.integration_ladder",
        "LastResortAgentProof",
        "ralph.pipeline.last_resort_agent_proof",
    ),
    (
        "ralph.pipeline.integration_ladder",
        "LastResortWaitProof",
        "ralph.pipeline.last_resort_wait_proof",
    ),
    ("ralph.recovery.agent_failure_ledger", "EligibilityWait", "ralph.recovery.eligibility_wait"),
    ("ralph.recovery.agent_failure_ledger", "WaitKind", "ralph.recovery.wait_kind"),
)


def _declared_public_classes(module_name: str) -> tuple[str, ...]:
    module = importlib.import_module(module_name)
    tree = ast.parse(inspect.getsource(module))
    return tuple(
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    )


@pytest.mark.parametrize(("module_name", "expected_class"), _AUDITED_MODULES)
def test_module_declares_at_most_one_public_top_level_class(
    module_name: str,
    expected_class: str | None,
) -> None:
    declared = _declared_public_classes(module_name)

    assert len(declared) <= 1
    if expected_class is not None:
        assert declared == (expected_class,)


@pytest.mark.parametrize(("facade", "class_name", "canonical"), _REEXPORTED_CLASSES)
def test_facade_reexports_the_canonical_class(
    facade: str,
    class_name: str,
    canonical: str,
) -> None:
    facade_module = importlib.import_module(facade)
    canonical_module = importlib.import_module(canonical)

    assert getattr(facade_module, class_name) is getattr(canonical_module, class_name)
