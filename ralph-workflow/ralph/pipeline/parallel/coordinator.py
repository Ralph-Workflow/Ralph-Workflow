"""Structured concurrency coordinator for parallel development fan-out."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph import logging as ralph_logging
from ralph.agents import subprocess_executor
from ralph.mcp.artifacts.store import list_artifacts
from ralph.mcp.protocol.env import (
    AGENT_LABEL_SCOPE_ENV,
    MCP_ENDPOINT_ENV,
    WORKER_ARTIFACT_DIR_ENV,
    WORKER_ID_ENV,
    WORKER_NAMESPACE_ENV,
)
from ralph.mcp.server import factory_impl
from ralph.pipeline.events import (
    Event,
    PipelineEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
    WorkerStartedEvent,
)
from ralph.pipeline.parallel import worker_session
from ralph.pipeline.parallel.scheduler import schedule_next_wave
from ralph.pipeline.work_units import (
    WorkUnitsPlan,
    WorkUnitsValidationError,
    validate_for_same_workspace,
)
from ralph.pipeline.worker_state import WorkerStatus
from ralph.process.manager import ProcessTerminationError, get_process_manager
from ralph.workspace import fs
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.agents.executor import AgentExecutor, WorkerResult
    from ralph.display.activity_router import ActivityRouter
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.effects import FanOutEffect
    from ralph.pipeline.parallel.mode import SameWorkspaceContext
    from ralph.pipeline.parallel.worker_session import WorkerSessionBundle
    from ralph.pipeline.work_units import WorkUnit


@dataclass(frozen=True)
class _WorkerLog:
    log_dir: Path
    run_id: str


@dataclass(frozen=True)
class _WorkerContext:
    log: _WorkerLog | None = None
    same_workspace: SameWorkspaceContext | None = None
    activity_router: ActivityRouter | None = None


class ParallelCoordinator:
    """Orchestrates parallel work-unit execution with DAG dependency ordering."""

    def __init__(self, *, activity_router: ActivityRouter | None = None) -> None:
        self.activity_router = activity_router

    async def run_fan_out(
        self,
        effect: FanOutEffect,
        executor: AgentExecutor,
        display: ParallelDisplay,
        ctx: _WorkerContext | None = None,
    ) -> list[Event]:
        """Execute parallel work units while respecting DAG dependencies and worker caps."""
        # Prefer the display's activity_router when the coordinator has none.
        effective_router = self.activity_router
        if effective_router is None and hasattr(display, "activity_router"):
            effective_router = display.activity_router

        worker_ctx = (
            _WorkerContext(activity_router=effective_router)
            if ctx is None
            else replace(ctx, activity_router=effective_router)
        )

        same_workspace = worker_ctx.same_workspace if worker_ctx is not None else None
        ns_root = (
            str(same_workspace.worker_namespace_root)
            if same_workspace is not None and same_workspace.worker_namespace_root is not None
            else "unknown"
        )
        logger.info(
            "fan-out start mode=same_workspace units={n} namespace_root={ns}",
            n=len(effect.work_units),
            ns=ns_root,
        )

        # Fail-closed preflight: validate that the plan is safe for same-workspace
        # execution before any worker is launched. This is a secondary guard; the
        # runner also validates before calling us. Direct coordinator callers (e.g.
        # tests, future tooling) are protected by this check too.
        if effect.work_units:
            try:
                validate_for_same_workspace(WorkUnitsPlan(work_units=list(effect.work_units)))
            except WorkUnitsValidationError as exc:
                logger.error("coordinator preflight rejected plan: {}", exc)
                return [
                    WorkerFailedEvent(
                        unit_id="__preflight__",
                        exit_code=2,
                        error=f"parallel preflight rejected plan: {exc}",
                    )
                ]

        events: list[Event] = [PipelineEvent.FAN_OUT_STARTED]
        if not effect.work_units:
            return [*events, PipelineEvent.ALL_WORKERS_COMPLETE]

        pending = {unit.unit_id for unit in effect.work_units}
        completed: set[str] = set()
        running: dict[str, WorkUnit] = {}
        completion_queue: asyncio.Queue[WorkerResult] = asyncio.Queue()

        try:
            async with asyncio.TaskGroup() as task_group:
                while pending or running:
                    ready = schedule_next_wave(
                        completed,
                        effect.work_units,
                        set(running),
                        effect.max_workers,
                    )

                    for unit in ready:
                        pending.discard(unit.unit_id)
                        running[unit.unit_id] = unit
                        events.append(WorkerStartedEvent(unit_id=unit.unit_id))
                        task_group.create_task(
                            _run_worker(
                                unit,
                                executor,
                                display,
                                completion_queue,
                                worker_ctx,
                            ),
                            name=unit.unit_id,
                        )

                    if running:
                        result = await completion_queue.get()
                        running.pop(result.unit_id, None)
                        completed.add(result.unit_id)
                        events.append(
                            WorkerCompletedEvent(
                                unit_id=result.unit_id,
                                exit_code=result.exit_code,
                            )
                        )
                        continue

                    if pending:
                        break
        except* Exception as group:
            failures, unexpected = _flatten_worker_failures(group.exceptions)
            _append_terminal_failure_events(
                events=events,
                work_units=effect.work_units,
                pending=pending,
                running=running,
                failures=failures,
            )
            if unexpected:
                raise ExceptionGroup("Unexpected fan-out coordinator failure", unexpected) from None
        else:
            events.append(PipelineEvent.ALL_WORKERS_COMPLETE)

        return events


class _WorkerFailureError(Exception):
    def __init__(self, unit_id: str, exit_code: int, error: str) -> None:
        super().__init__(error)
        self.unit_id = unit_id
        self.exit_code = exit_code
        self.error = error


def _flatten_worker_failures(
    exceptions: tuple[BaseException, ...],
) -> tuple[list[_WorkerFailureError], list[Exception]]:
    failures: list[_WorkerFailureError] = []
    unexpected: list[Exception] = []
    stack = list(exceptions)

    while stack:
        current = stack.pop()
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
            continue
        if isinstance(current, _WorkerFailureError):
            failures.append(current)
            continue
        if isinstance(current, Exception):
            unexpected.append(current)

    return failures, unexpected


def _prepare_executor(
    unit: WorkUnit,
    executor: AgentExecutor,
    same_workspace: SameWorkspaceContext | None,
    activity_router: ActivityRouter | None = None,
) -> tuple[AgentExecutor, WorkerSessionBundle | None, Path | None]:
    if same_workspace is None:
        if activity_router is not None and isinstance(
            executor, subprocess_executor.SubprocessAgentExecutor
        ):
            executor.activity_router = activity_router
        return executor, None, None

    ns_root = same_workspace.worker_namespace_root or (
        same_workspace.repo_root / ".agent" / "workers"
    )
    worker_namespace = ns_root / unit.unit_id
    for subdir in ("artifacts", "tmp", "logs", "handoffs"):
        (worker_namespace / subdir).mkdir(parents=True, exist_ok=True)

    worker_scope = WorkspaceScope.for_same_workspace_worker(
        repo_root=same_workspace.repo_root,
        allowed_directories=tuple(unit.allowed_directories),
        worker_namespace=worker_namespace,
    )

    if same_workspace.executor_command is None:
        # In-process mode (e.g. tests with FakeAgentExecutor): use the injected
        # mcp_factory directly rather than spinning up a real MCP server.
        bundle = worker_session.build_worker_session(
            unit,
            same_workspace.mcp_factory,
            worker_scope,
            worker_artifact_dir=worker_namespace / "artifacts",
            worker_namespace=worker_namespace,
            session_drain=same_workspace.session_drain,
            session_capabilities=same_workspace.session_capabilities,
            session_model_identity=same_workspace.session_model_identity,
            session_capability_profile=same_workspace.session_capability_profile,
        )
        return executor, bundle, worker_namespace

    worker_workspace = fs.FsWorkspace(
        same_workspace.repo_root,
        allowed_roots=worker_scope.allowed_roots,
    )
    worker_mcp_factory = factory_impl.DynamicBindingMcpServerFactory(workspace=worker_workspace)
    bundle = worker_session.build_worker_session(
        unit,
        worker_mcp_factory,
        worker_scope,
        worker_artifact_dir=worker_namespace / "artifacts",
        worker_namespace=worker_namespace,
        session_drain=same_workspace.session_drain,
        session_capabilities=same_workspace.session_capabilities,
        session_model_identity=same_workspace.session_model_identity,
        session_capability_profile=same_workspace.session_capability_profile,
    )
    worker_artifact_dir = worker_namespace / "artifacts"
    agent_label_scope = bundle.session.session_id
    return (
        cast(
            "AgentExecutor",
            subprocess_executor.SubprocessAgentExecutor(
                same_workspace.executor_command,
                signal_bridge=same_workspace.signal_bridge,
                cwd=same_workspace.repo_root,
                extra_env={
                    str(MCP_ENDPOINT_ENV): bundle.mcp_handle.endpoint,
                    str(WORKER_ID_ENV): unit.unit_id,
                    str(WORKER_NAMESPACE_ENV): str(worker_namespace),
                    str(WORKER_ARTIFACT_DIR_ENV): str(worker_artifact_dir),
                    str(AGENT_LABEL_SCOPE_ENV): agent_label_scope,
                },
                activity_router=activity_router,
                raw_overflow_root=worker_namespace / "logs",
            ),
        ),
        bundle,
        worker_namespace,
    )


def _blocked_dependency_error(unit: WorkUnit, failed_unit_ids: set[str]) -> str | None:
    blocked_by = sorted(dep for dep in unit.dependencies if dep in failed_unit_ids)
    if not blocked_by:
        return None
    return f"Blocked by failed dependencies: {', '.join(blocked_by)}"


def _blocked_pending_failures(
    work_units: tuple[WorkUnit, ...],
    pending_unit_ids: set[str],
    failed_unit_ids: set[str],
) -> list[WorkerFailedEvent]:
    pending_units = {unit.unit_id: unit for unit in work_units if unit.unit_id in pending_unit_ids}
    blocked_events: list[WorkerFailedEvent] = []
    expanded_failures = set(failed_unit_ids)

    while True:
        progress_made = False
        for unit_id, unit in list(pending_units.items()):
            blocked_error = _blocked_dependency_error(unit, expanded_failures)
            if blocked_error is None:
                continue
            blocked_events.append(
                WorkerFailedEvent(unit_id=unit_id, exit_code=1, error=blocked_error)
            )
            expanded_failures.add(unit_id)
            del pending_units[unit_id]
            progress_made = True
        if not progress_made:
            return blocked_events


def _append_terminal_failure_events(
    *,
    events: list[Event],
    work_units: tuple[WorkUnit, ...],
    pending: set[str],
    running: dict[str, WorkUnit],
    failures: list[_WorkerFailureError],
) -> None:
    seen_failures = {event.unit_id for event in events if isinstance(event, WorkerFailedEvent)}
    failed_unit_ids = {failure.unit_id for failure in failures}

    for failure in failures:
        if failure.unit_id in seen_failures:
            continue
        running.pop(failure.unit_id, None)
        events.append(
            WorkerFailedEvent(
                unit_id=failure.unit_id,
                exit_code=failure.exit_code,
                error=failure.error,
            )
        )
        seen_failures.add(failure.unit_id)

    for unit_id in list(running):
        if unit_id in seen_failures:
            continue
        events.append(
            WorkerFailedEvent(
                unit_id=unit_id,
                exit_code=1,
                error="Cancelled because another worker failed",
            )
        )
        seen_failures.add(unit_id)

    blocked_events = _blocked_pending_failures(
        work_units,
        pending,
        failed_unit_ids | seen_failures,
    )
    for blocked_event in blocked_events:
        if blocked_event.unit_id in seen_failures:
            continue
        events.append(blocked_event)
        seen_failures.add(blocked_event.unit_id)


async def _run_worker(
    unit: WorkUnit,
    executor: AgentExecutor,
    display: ParallelDisplay,
    completion_queue: asyncio.Queue[WorkerResult],
    ctx: _WorkerContext | None = None,
) -> None:
    log = ctx.log if ctx is not None else None
    same_workspace = ctx.same_workspace if ctx is not None else None
    activity_router = ctx.activity_router if ctx is not None else None

    with logger.contextualize(unit_id=unit.unit_id):
        sink_handle = (
            ralph_logging.bind_worker_sink(
                unit_id=unit.unit_id, log_dir=log.log_dir, run_id=log.run_id
            )
            if log is not None
            else None
        )

        bundle = None
        worker_succeeded = False
        active_executor = executor

        def on_output(line: str) -> None:
            display.emit(unit.unit_id, line)

        def on_status(status: WorkerStatus) -> None:
            display.set_status(unit.unit_id, status)

        try:
            active_executor, bundle, worker_namespace = _prepare_executor(
                unit,
                executor,
                same_workspace,
                activity_router,
            )

            try:
                result = await active_executor.run(unit, on_output=on_output, on_status=on_status)
            except asyncio.CancelledError:
                display.set_status(unit.unit_id, WorkerStatus.CANCELLED)
                raise

            except BaseException as exc:
                if isinstance(exc, _WorkerFailureError):
                    raise
                if isinstance(exc, Exception):
                    display.set_status(unit.unit_id, WorkerStatus.FAILED)
                    raise _WorkerFailureError(unit.unit_id, 1, str(exc)) from exc
                raise

            # When running in same-workspace mode (bundle is not None), worker success
            # is determined exclusively by worker-local artifact evidence. Repo-wide
            # git status is never used as a fallback signal.
            if bundle is not None and worker_namespace is not None:
                artifact_dir = worker_namespace / "artifacts"
                if not list_artifacts(artifact_dir):
                    display.set_status(unit.unit_id, WorkerStatus.FAILED)
                    raise _WorkerFailureError(
                        unit_id=unit.unit_id,
                        exit_code=result.exit_code,
                        error=(
                            f"Worker {unit.unit_id!r} produced no worker-local artifact "
                            f"evidence under {artifact_dir} "
                            f"(exit_code={result.exit_code})"
                        ),
                    )

            display.set_status(unit.unit_id, WorkerStatus.SUCCEEDED)
            await completion_queue.put(result)
            worker_succeeded = True
        finally:
            if bundle is not None:
                bundle.mcp_handle.shutdown()
            label_env: dict[str, str] | None = None
            if bundle is not None and same_workspace is not None:
                label_env = {str(AGENT_LABEL_SCOPE_ENV): bundle.session.session_id}
            try:
                get_process_manager().shutdown_all_for_label(
                    subprocess_executor.agent_process_label_prefix(unit.unit_id, label_env),
                    grace_period_s=2.0,
                )
            except ProcessTerminationError as exc:
                logger.error(
                    "Failed to terminate agent processes for worker {}: {}", unit.unit_id, exc
                )
            if sink_handle is not None:
                ralph_logging.remove_worker_sink(sink_handle)
            del worker_succeeded


async def run_fan_out(
    effect: FanOutEffect,
    executor: AgentExecutor,
    display: ParallelDisplay,
    ctx: _WorkerContext | None = None,
    activity_router: ActivityRouter | None = None,
) -> list[Event]:
    """Execute a fan-out effect using a fresh ParallelCoordinator instance."""
    coordinator = ParallelCoordinator(activity_router=activity_router)
    return await coordinator.run_fan_out(effect, executor, display, ctx)


__all__ = ["ParallelCoordinator", "run_fan_out"]
