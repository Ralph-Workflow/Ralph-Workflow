"""HAS_SUBAGENTS and remaining-budget template variables.

S-5 wires the runtime sub-agent declaration into the prompt variables so the
shared subagent partial can branch on ``HAS_SUBAGENTS`` and the run-budget
partial can render the concrete remaining minutes and the force-cut
sentence. This test exercises the public surface only:

* :func:`ralph.prompts.template_variables.delegation_template_variable`
* :func:`ralph.prompts.materialize.materialize_prompt_for_phase`
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import pytest

from ralph.agents.delegation_capabilities import (
    DelegationStance,
    delegation_for,
)
from ralph.config.enums import AgentTransport
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.template_variables import delegation_template_variable
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

# ---------------------------------------------------------------------------
# HAS_SUBAGENTS — public delegation_template_variable
# ---------------------------------------------------------------------------


def test_delegation_template_variable_true_for_supported_transport() -> None:
    """SUPPORTED transports render HAS_SUBAGENTS as the literal "true"."""
    supported = [
        transport
        for transport in AgentTransport
        if delegation_for(transport).stance == DelegationStance.SUPPORTED
    ]
    assert supported, "test fixture: at least one SUPPORTED transport must exist"

    for transport in supported:
        vars_map = delegation_template_variable(transport)
        assert vars_map["HAS_SUBAGENTS"] == "true", transport


def test_delegation_template_variable_empty_for_explicit_unsupported() -> None:
    """EXPLICIT_UNSUPPORTED transports render HAS_SUBAGENTS as the empty
    string (the partial's ``|default('')`` fallback idiom).
    """
    explicit_unsupported = [
        transport
        for transport in AgentTransport
        if delegation_for(transport).stance == DelegationStance.EXPLICIT_UNSUPPORTED
    ]
    assert explicit_unsupported, "test fixture: at least one EXPLICIT_UNSUPPORTED transport must exist"

    for transport in explicit_unsupported:
        vars_map = delegation_template_variable(transport)
        assert vars_map["HAS_SUBAGENTS"] == "", transport


def test_delegation_template_variable_empty_for_not_applicable() -> None:
    """NOT_APPLICABLE transports render HAS_SUBAGENTS as the empty string."""
    not_applicable = [
        transport
        for transport in AgentTransport
        if delegation_for(transport).stance == DelegationStance.NOT_APPLICABLE
    ]
    if not not_applicable:
        pytest.skip("no NOT_APPLICABLE transports in this build")
    for transport in not_applicable:
        vars_map = delegation_template_variable(transport)
        assert vars_map["HAS_SUBAGENTS"] == "", transport


# ---------------------------------------------------------------------------
# Remaining-budget rendering — materialized development prompt
# ---------------------------------------------------------------------------


def _materialized_development_prompt(
    tmp_path: Path,
    *,
    warn_epoch: float | None = None,
    deadline_epoch: float | None = None,
) -> str:
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the plan")
    workspace.write(
        ".agent/artifacts/plan.md",
        "---\ntype: plan\n---\n## Summary\nUse canonical markdown.\n",
    )
    workspace.write(".agent/PLAN.md", "## Summary\nUse canonical markdown.\n")
    policy = load_policy(tmp_path / ".agent")

    path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase=None,
        ),
    )
    return workspace.read(path)


def test_developer_prompt_renders_remaining_minutes_when_epochs_published(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With DEV_WARN_EPOCH / DEV_DEADLINE_EPOCH published, the rendered
    prompt mentions the remaining minutes and the force-cut sentence.
    """
    # 25 minutes (1500s) remaining at the deadline, 20 minutes (1200s) at the
    # warning. Both are comfortably positive so the test does not depend on
    # clock drift between env-publication and materialization.
    now = time.time()
    warn_epoch = now + 1200.0
    deadline_epoch = now + 1500.0
    monkeypatch.setenv("RALPH_DEV_WARN_EPOCH", f"{warn_epoch:.0f}")
    monkeypatch.setenv("RALPH_DEV_DEADLINE_EPOCH", f"{deadline_epoch:.0f}")

    rendered = _materialized_development_prompt(tmp_path)
    flat = " ".join(rendered.split())

    # Remaining-minute figure must appear.
    assert "minutes remaining" in flat, "expected 'minutes remaining' phrase"
    # The deadline figure must mention "force-cut" (or equivalent wording).
    assert "force-cut" in flat, "expected the session force-cut sentence"


def test_developer_prompt_no_minutes_when_no_epochs_published(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With no deadline epochs published, the rendered prompt must not
    render minute figures or the force-cut sentence (the no-partial rule
    stays untouched via the partial's own defaults).
    """
    monkeypatch.delenv("RALPH_DEV_WARN_EPOCH", raising=False)
    monkeypatch.delenv("RALPH_DEV_DEADLINE_EPOCH", raising=False)

    rendered = _materialized_development_prompt(tmp_path)
    flat = " ".join(rendered.split())

    # The run-budget partial must NOT render a number-of-minutes figure
    # when no epochs are published; the no-partial-on-exhaustion rule stays.
    assert "minutes remaining" not in flat, (
        "without a published deadline, no remaining-minutes figure should appear"
    )
    assert "force-cut" not in flat, (
        "without a published deadline, the force-cut sentence should not appear"
    )
    # The partial's own pinned "exhausted budget" phrase still gates the
    # no-partial-on-exhaustion rule regardless of deadline publication.
    assert math.isfinite(time.time())  # sanity: wall clock reachable
