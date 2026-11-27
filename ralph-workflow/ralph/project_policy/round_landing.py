from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.registry import AgentRegistry
from ralph.git._dirty_paths import list_dirty_paths
from ralph.git.operations import create_commit, stage_files
from ralph.git.scoped_auto_commit import commit_scoped_updates
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_agent import (
    build_agent_conflict_resolver,
    build_agent_rebase_stop_resolver,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.project_policy.finalization import commit_policy_changes as _commit_policy_changes

if TYPE_CHECKING:
    from ralph.cli.commands._load_result import _LoadResult
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.factory import PipelineDeps
    from ralph.workspace.scope import WorkspaceScope


PolicyRoundLanding = Callable[["WorkspaceScope", "UnifiedConfig", frozenset[str] | None], None]

_GUARD_RAIL_COMMIT_SUBJECT = "build(policy): set up guard rails"
_AGENT_STATE_PREFIX = ".agent/"


def _commit_guard_rail_changes(scope: WorkspaceScope, pre_run_dirty: frozenset[str] | None) -> None:
    root = Path(scope.root)
    changed = sorted(
        path
        for path in list_dirty_paths(root) - (pre_run_dirty or frozenset())
        if not path.startswith(_AGENT_STATE_PREFIX)
    )
    if not changed:
        return
    commit_scoped_updates(
        root,
        scopes=tuple(changed),
        subject=_GUARD_RAIL_COMMIT_SUBJECT,
        body_builder=lambda paths: (
            "Guard rails set up by policy remediation:\n" + "\n".join(f"- {path}" for path in paths)
        ),
        create_commit_fn=create_commit,
        stage_fn=stage_files,
    )


def round_landing(
    *,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> PolicyRoundLanding:
    return functools.partial(
        _land_policy_round,
        conflict_resolver=conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
    )


def agent_round_landing(
    *,
    load_result: _LoadResult,
    display: ParallelDisplay,
    display_context: DisplayContext,
    pipeline_deps: PipelineDeps | None,
) -> PolicyRoundLanding:
    policy_bundle = load_result.policy_bundle
    if policy_bundle is None or pipeline_deps is None:
        return _land_policy_round
    registry = AgentRegistry.from_config(load_result.config)
    scope = load_result.workspace_scope
    return round_landing(
        conflict_resolver=build_agent_conflict_resolver(
            policy_bundle=policy_bundle,
            registry=registry,
            display=display,
            config=load_result.config,
            pipeline_deps=pipeline_deps,
            workspace_scope=scope,
            display_context=display_context,
        ),
        rebase_stop_resolver=build_agent_rebase_stop_resolver(
            policy_bundle=policy_bundle,
            registry=registry,
            display=display,
            config=load_result.config,
            pipeline_deps=pipeline_deps,
            workspace_scope=scope,
            display_context=display_context,
        ),
    )


ReadinessLanding = Callable[["WorkspaceScope", "UnifiedConfig"], None]


def land_readiness_commits(scope: WorkspaceScope, config: UnifiedConfig) -> None:
    try:
        auto_integrate_after_commit(config, scope, RebaseState())
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        logger.warning("landing the readiness commits failed: {}", exc)


_SET_ASIDE_MESSAGE = "ralph: development work set aside while a policy round lands"


@contextmanager
def _development_work_set_aside(root: Path) -> Iterator[None]:
    if not list_dirty_paths(root):
        yield
        return
    pushed = run_git(
        ("stash", "push", "--include-untracked", "--message", _SET_ASIDE_MESSAGE),
        cwd=root,
        label="policy-round:set-aside",
    )
    if pushed.returncode != 0:
        detail = (pushed.stderr or pushed.stdout).strip()[:200]
        raise RuntimeError(f"could not set development work aside: {detail}")
    try:
        yield
    finally:
        restored = run_git(("stash", "pop", "--index"), cwd=root, label="policy-round:restore")
        if restored.returncode != 0:
            restored = run_git(("stash", "pop"), cwd=root, label="policy-round:restore")
        if restored.returncode != 0:
            logger.error(
                "development work stays in the stash '{}': {}",
                _SET_ASIDE_MESSAGE,
                (restored.stderr or restored.stdout).strip()[:200],
            )


def _land_policy_round(
    scope: WorkspaceScope,
    config: UnifiedConfig,
    pre_run_dirty: frozenset[str] | None,
    *,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> None:
    try:
        _commit_policy_changes(scope, pre_run_dirty)
        _commit_guard_rail_changes(scope, pre_run_dirty)
        with _development_work_set_aside(Path(scope.root)):
            auto_integrate_after_commit(
                config,
                scope,
                RebaseState(),
                conflict_resolver=conflict_resolver,
                rebase_stop_resolver=rebase_stop_resolver,
            )
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        logger.warning("landing the policy remediation round failed: {}", exc)
