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
    now = time.time()
    warn_epoch = now + 1200.0
    deadline_epoch = now + 1500.0
    monkeypatch.setenv("RALPH_DEV_WARN_EPOCH", f"{warn_epoch:.0f}")
    monkeypatch.setenv("RALPH_DEV_DEADLINE_EPOCH", f"{deadline_epoch:.0f}")

    rendered = _materialized_development_prompt(tmp_path)
    flat = " ".join(rendered.split())

    assert "minutes remaining" in flat, "expected 'minutes remaining' phrase"
    assert "force-cut" in flat, "expected the session force-cut sentence"
    assert "warning point" in flat, "expected the warning-point countdown"
    assert "unsubmitted artifact is lost" in flat
    assert "submit before the deadline" in flat
    assert "three unrelated areas" in flat
    assert "four independent criteria" in flat


def test_developer_prompt_no_minutes_when_no_epochs_published(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("RALPH_DEV_WARN_EPOCH", raising=False)
    monkeypatch.delenv("RALPH_DEV_DEADLINE_EPOCH", raising=False)

    rendered = _materialized_development_prompt(tmp_path)
    flat = " ".join(rendered.split())

    assert "minutes remaining" not in flat, (
        "without a published deadline, no remaining-minutes figure should appear"
    )
    assert "force-cut" not in flat, (
        "without a published deadline, the force-cut sentence should not appear"
    )
    assert "warning point" not in flat, (
        "without a published warning, no warning-point countdown should appear"
    )
    assert math.isfinite(time.time())


def test_timebox_template_variables_renders_warning_countdown() -> None:
    from ralph.prompts.template_variables import timebox_template_variables

    now = 1_000_000.0
    warn_epoch = now + 600.0
    deadline_epoch = now + 1500.0
    vars_map = timebox_template_variables(
        warn_epoch=warn_epoch,
        deadline_epoch=deadline_epoch,
        now_epoch=now,
    )
    assert vars_map["DEV_WARN_REMAINING_MINUTES"] == "10"
    assert vars_map["DEV_REMAINING_MINUTES"] == "25"
    assert vars_map["DEV_FORCE_CUT"] == "true"

    assert (
        timebox_template_variables(warn_epoch=None, deadline_epoch=deadline_epoch, now_epoch=now)
        == {}
    )
    assert (
        timebox_template_variables(warn_epoch=warn_epoch, deadline_epoch=None, now_epoch=now) == {}
    )
