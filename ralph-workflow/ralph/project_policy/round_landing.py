from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.registry import AgentRegistry
from ralph.git._dirty_paths import list_dirty_paths
from ralph.git.merge import stage_paths, unmerged_paths
from ralph.git.operations import create_commit, stage_files
from ralph.git.scoped_auto_commit import commit_scoped_updates
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate import auto_integrate_after_commit, resolve_integration_target
from ralph.pipeline.auto_integrate_agent import (
    build_agent_conflict_resolver,
    build_agent_rebase_stop_resolver,
)
from ralph.pipeline.conflict_resolution.pipeline_entrypoints import run_conflict_resolution_pipeline
from ralph.pipeline.rebase_state import RebaseState
from ralph.project_policy.finalization import commit_policy_changes as _commit_policy_changes
from ralph.project_policy.guard_downgrades import downgraded_guards
from ralph.project_policy.markers import CANONICAL_DIR

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
    restore_conflict_resolver: RestoreConflictResolver | None = None,
) -> PolicyRoundLanding:
    return functools.partial(
        _land_policy_round,
        conflict_resolver=conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
        restore_conflict_resolver=restore_conflict_resolver,
    )


def _resolve_restore_with_agents(
    root: Path,
    *,
    load_result: _LoadResult,
    display: ParallelDisplay,
    display_context: DisplayContext,
    pipeline_deps: PipelineDeps,
) -> bool:
    policy_bundle = load_result.policy_bundle
    scope = load_result.workspace_scope
    if policy_bundle is None or scope is None:
        return False
    try:
        target = resolve_integration_target(load_result.config, root)
        if target is None:
            return False
        return run_conflict_resolution_pipeline(
            root=root,
            target=target,
            config=load_result.config,
            pipeline_deps=pipeline_deps,
            workspace_scope=scope,
            policy_bundle=policy_bundle,
            display=display,
            display_context=display_context,
        )
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("could not resolve the restored development work: {}", exc)
        return False


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
        restore_conflict_resolver=functools.partial(
            _resolve_restore_with_agents,
            load_result=load_result,
            display=display,
            display_context=display_context,
            pipeline_deps=pipeline_deps,
        ),
    )


ReadinessLanding = Callable[["WorkspaceScope", "UnifiedConfig"], None]


def land_readiness_commits(scope: WorkspaceScope, config: UnifiedConfig) -> None:
    try:
        auto_integrate_after_commit(config, scope, RebaseState())
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        logger.warning("landing the readiness commits failed: {}", exc)


_SET_ASIDE_MESSAGE = "ralph: development work set aside while a policy round lands"


RestoreConflictResolver = Callable[[Path], bool]


def _resolve_conflicted_restore(root: Path, resolver: RestoreConflictResolver | None) -> bool:
    paths = tuple(path for path in unmerged_paths(root) if path != "<unmerged-path-query-failed>")
    if not paths:
        return False
    if resolver is None or not resolver(root) or not stage_paths(root, paths):
        return False
    if unmerged_paths(root):
        return False
    run_git(("stash", "drop"), cwd=root, label="policy-round:drop-restored")
    logger.info(
        "resolved {} path(s) where the restored development work conflicted with the round",
        len(paths),
    )
    return True


@contextmanager
def _development_work_set_aside(
    root: Path, resolver: RestoreConflictResolver | None = None
) -> Iterator[None]:
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
        if restored.returncode != 0 and not _resolve_conflicted_restore(root, resolver):
            logger.error(
                "development work stays in the stash '{}': {}",
                _SET_ASIDE_MESSAGE,
                (restored.stderr or restored.stdout).strip()[:200],
            )


def _round_guard_downgrades(root: Path) -> tuple[str, ...]:
    result = run_git(
        ("diff", "-U0", "--no-color", "--no-ext-diff", "HEAD", "--", CANONICAL_DIR),
        cwd=root,
        label="policy-round:guard-downgrade-diff",
    )
    if result.returncode != 0 or not result.stdout:
        return ()
    return downgraded_guards(result.stdout)


def _land_policy_round(
    scope: WorkspaceScope,
    config: UnifiedConfig,
    pre_run_dirty: frozenset[str] | None,
    *,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    restore_conflict_resolver: RestoreConflictResolver | None = None,
) -> None:
    try:
        downgrades = _round_guard_downgrades(Path(scope.root))
        if downgrades:
            logger.warning(
                "not landing this policy round: it downgrades {} guard(s) from deterministic to "
                "advisory ({}); the round stays uncommitted for analysis to send back",
                len(downgrades),
                "; ".join(downgrades[:5]),
            )
            return
        _commit_policy_changes(scope, pre_run_dirty)
        _commit_guard_rail_changes(scope, pre_run_dirty)
        with _development_work_set_aside(Path(scope.root), restore_conflict_resolver):
            auto_integrate_after_commit(
                config,
                scope,
                RebaseState(),
                conflict_resolver=conflict_resolver,
                rebase_stop_resolver=rebase_stop_resolver,
            )
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        logger.warning("landing the policy remediation round failed: {}", exc)
