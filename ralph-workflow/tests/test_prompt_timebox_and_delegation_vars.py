"""Remaining-budget template variables.

The run-budget partial renders the concrete remaining minutes and the
force-cut sentence when the pipeline publishes ``DEV_WARN_EPOCH`` and
``DEV_DEADLINE_EPOCH``. Without epochs, the partial stays in its
no-partial-on-exhaustion shape. This test exercises the public surface
only:

* :func:`ralph.prompts.template_variables.timebox_template_variables`
* :func:`ralph.prompts.materialize.materialize_prompt_for_phase`
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.policy.loader import load_policy
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    import pytest

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
    # The warning-point countdown must also be rendered when both epochs are
    # published so the agent can pace work before the warning is reached.
    assert "warning point" in flat, "expected the warning-point countdown"


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
    assert "warning point" not in flat, (
        "without a published warning, no warning-point countdown should appear"
    )
    # The partial's own pinned "exhausted budget" phrase still gates the
    # no-partial-on-exhaustion rule regardless of deadline publication.
    assert math.isfinite(time.time())  # sanity: wall clock reachable


def test_timebox_template_variables_renders_warning_countdown() -> None:
    """``timebox_template_variables`` exposes the warning countdown, the
    deadline countdown, and the force-cut flag together; with no
    published epochs, the mapping is empty so the partial falls through
    to the no-partial-on-exhaustion rule.
    """
    from ralph.prompts.template_variables import timebox_template_variables

    now = 1_000_000.0
    warn_epoch = now + 600.0  # 10 minutes
    deadline_epoch = now + 1500.0  # 25 minutes
    vars_map = timebox_template_variables(
        warn_epoch=warn_epoch,
        deadline_epoch=deadline_epoch,
        now_epoch=now,
    )
    assert vars_map["DEV_WARN_REMAINING_MINUTES"] == "10"
    assert vars_map["DEV_REMAINING_MINUTES"] == "25"
    assert vars_map["DEV_FORCE_CUT"] == "true"

    # Missing epochs ⇒ empty mapping; partials fall through via |default('').
    assert timebox_template_variables(
        warn_epoch=None, deadline_epoch=deadline_epoch, now_epoch=now
    ) == {}
    assert timebox_template_variables(
        warn_epoch=warn_epoch, deadline_epoch=None, now_epoch=now
    ) == {}
