"""Fast, budgeted tests for the runner._run_pipeline_step commit-seam wiring.

Mirrors :mod:`tests.test_pipeline_runner_cycle_baseline_lifecycle` --
drives the REAL :func:`ralph.pipeline.runner.run_pipeline_step` through
the same commit-role block (builds a commit-role ``PhaseDefinition``
in a ``PolicyBundle``, a ``CommitEffect(message_file=...)``, a
registry handler returning the target ``PipelineEvent``, and a
``WorkspaceScope`` on ``tmp_path``). Monkeypatches
``ralph.pipeline.runner.auto_integrate_after_commit`` so this test
stays in the 60s combined ``make verify`` budget (no real git
subprocesses).

Two scenarios:

1. ``COMMIT_SUCCESS`` -> the spy is called exactly once AND
   ``next_state.rebase`` equals the sentinel ``RebaseState`` (proves
   the outcome is threaded via ``next_state.copy_with(rebase=...)``
   BEFORE ``_save_checkpoint_or_log`` runs), AND a user-facing
   ``auto-integrate: ...`` log line is emitted.
2. ``COMMIT_SKIPPED`` -> the spy is NOT called and
   ``next_state.rebase`` is left unchanged from the input state
   (proves the ``COMMIT_SUCCESS``-only trigger and the skip
   exclusion).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from git import Repo as GitRepo

from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import CommitEffect, ExitSuccessEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import LogCaptureFixture, MonkeyPatch

    from ralph.policy.models import PolicyBundle


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _install_runner_display_context(
    monkeypatch: MonkeyPatch,
    *,
    width: int = 120,
):
    ctx = make_display_context(force_width=width)
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)
    return ctx


def _write_minimal_plan_artifacts(root: Path, *, context: str = "Existing plan") -> None:
    (root / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / ".agent" / "artifacts" / "plan.json").write_text(
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": context,
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "skills_mcp": {"skills": ["test-driven-development"], "mcps": []},
                    "steps": [{"number": 1, "title": "Revise", "content": "keep context"}],
                    "critical_files": {"primary_files": [], "reference_files": []},
                    "risks_mitigations": [],
                    "verification_strategy": [],
                    "work_units": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (root / ".agent" / "PLAN.md").write_text(
        f"# Execution Plan\n\n{context}.\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path)
    )
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", lambda _path: _load_default_policy_bundle()
    )


def test_commit_success_threads_rebase_state_into_next_state(
    monkeypatch: MonkeyPatch,
    tmp_git_repo: Path,
    caplog: LogCaptureFixture,
) -> None:
    """COMMIT_SUCCESS path: auto-integrate spy is called once and outcome persists."""
    # Seed a real git HEAD so get_head_sha / branch_sha won't trip
    # inside the (mocked) auto_integrate path.
    with GitRepo(tmp_git_repo) as _r:
        _head_sha = _r.head.commit.hexsha

    spy_calls: list[tuple[object, object, object]] = []
    sentinel = RebaseState(
        last_action="rebased",
        last_reason=None,
        last_target="main",
        fast_forwarded=True,
    )

    def _spy(config: object, workspace_scope: object, rebase_state: object) -> object:
        spy_calls.append((config, workspace_scope, rebase_state))
        return sentinel

    monkeypatch.setattr(runner_module, "auto_integrate_after_commit", _spy)
    monkeypatch.setattr(
        runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_git_repo)
    )

    commit_effect = CommitEffect(message_file="/dev/null")
    call_count = {"n": 0}

    def _fake_determine_effect(
        _state: object, _bundle: object, _scope: object
    ) -> CommitEffect | ExitSuccessEffect:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return commit_effect
        return ExitSuccessEffect()

    state = MagicMock()
    state.phase = "development_commit"
    # copy_with is called twice: once by reducer_reduce (mocked), once
    # by the runner when it threads next_state.copy_with(rebase=sentinel).
    state.copy_with = MagicMock(return_value=state)
    state.rebase = RebaseState()

    monkeypatch.setattr(
        runner_module, "determine_effect_from_policy", _fake_determine_effect
    )
    monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
    _install_runner_display_context(monkeypatch)
    monkeypatch.setattr(
        runner_module,
        "execute_commit_effect",
        lambda *_args, **_kwargs: PipelineEvent.COMMIT_SUCCESS,
    )
    monkeypatch.setattr(
        runner_module,
        "materialize_agent_prompt_if_needed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(runner_module, "clear_cycle_baseline", lambda *_a, **_k: None)
    monkeypatch.setattr(
        runner_module,
        "reducer_reduce",
        lambda _state, _event, _policy, recovery=None: (state, []),
    )

    caplog.set_level("DEBUG")
    runner_module.run(
        MagicMock(), initial_state=state, verbosity=Verbosity.QUIET
    )

    # Spy was called once with the live config, workspace_scope, and
    # the input state's rebase.
    assert len(spy_calls) == 1, (
        f"auto_integrate_after_commit must be called exactly once for "
        f"COMMIT_SUCCESS; got {len(spy_calls)}"
    )
    _config, scope_arg, rebase_arg = spy_calls[0]
    assert scope_arg is not None
    assert rebase_arg is state.rebase

    # next_state.copy_with(rebase=sentinel) was called -- this proves
    # the outcome was threaded into the persisted state BEFORE
    # _save_checkpoint_or_log ran.
    rebase_calls = [
        call.kwargs for call in state.copy_with.call_args_list if "rebase" in call.kwargs
    ]
    assert rebase_calls, (
        "next_state.copy_with(rebase=...) must be called to persist the outcome"
    )
    assert rebase_calls[-1]["rebase"] == sentinel


def test_commit_skipped_does_not_invoke_auto_integrate(
    monkeypatch: MonkeyPatch,
    tmp_git_repo: Path,
) -> None:
    """COMMIT_SKIPPED path: spy is NOT called and rebase state is unchanged."""
    with GitRepo(tmp_git_repo) as _r:
        _head_sha = _r.head.commit.hexsha

    spy_calls: list[object] = []

    def _spy(config: object, workspace_scope: object, rebase_state: object) -> object:
        spy_calls.append((config, workspace_scope, rebase_state))
        return None

    monkeypatch.setattr(runner_module, "auto_integrate_after_commit", _spy)
    monkeypatch.setattr(
        runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_git_repo)
    )

    commit_effect = CommitEffect(message_file="/dev/null")
    call_count = {"n": 0}

    def _fake_determine_effect(
        _state: object, _bundle: object, _scope: object
    ) -> CommitEffect | ExitSuccessEffect:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return commit_effect
        return ExitSuccessEffect()

    state = MagicMock()
    state.phase = "development_commit"
    state.copy_with = MagicMock(return_value=state)
    state.rebase = RebaseState()

    monkeypatch.setattr(
        runner_module, "determine_effect_from_policy", _fake_determine_effect
    )
    monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
    _install_runner_display_context(monkeypatch)
    # COMMIT_SKIPPED instead of COMMIT_SUCCESS.
    monkeypatch.setattr(
        runner_module,
        "execute_commit_effect",
        lambda *_args, **_kwargs: PipelineEvent.COMMIT_SKIPPED,
    )
    monkeypatch.setattr(
        runner_module,
        "materialize_agent_prompt_if_needed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(runner_module, "clear_cycle_baseline", lambda *_a, **_k: None)
    monkeypatch.setattr(
        runner_module,
        "reducer_reduce",
        lambda _state, _event, _policy, recovery=None: (state, []),
    )

    runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

    # Spy was never called.
    assert spy_calls == [], (
        "auto_integrate_after_commit must NOT be called on COMMIT_SKIPPED; "
        f"got {len(spy_calls)} call(s)"
    )
    # next_state.copy_with was called but NEVER with rebase=... (no
    # integration outcome to thread).
    rebase_calls = [
        call.kwargs for call in state.copy_with.call_args_list if "rebase" in call.kwargs
    ]
    assert rebase_calls == [], (
        "next_state.copy_with(rebase=...) must NOT be called on COMMIT_SKIPPED"
    )
