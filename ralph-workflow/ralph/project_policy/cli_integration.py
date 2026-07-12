"""Run-pipeline integration helpers for the project-policy-readiness preflight.

The preflight lives in :mod:`ralph.project_policy`; the run-pipeline CLI
in :mod:`ralph.cli.commands.run` only needs the orchestrator entry point
plus the dependency-injection helpers (workspace factory, emit factory,
remediation-agent factory). Moving the helpers here keeps the CLI module
under the 1000-line repository cap without dragging the orchestrator's
helpers into the public package surface.

Every helper here is independent and small; the orchestrator entry point
:func:`run_project_policy_readiness` stitches them together so the CLI
call site reads as one line.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

import typer
from loguru import logger

from ralph.agents.chain import ChainManager, DrainNotBoundError
from ralph.display.parallel_display import phase_style_for_phase, resolve_active_display
from ralph.display.status_bar import StatusBarModel
from ralph.git.operations import create_commit
from ralph.language_detector import get_project_stack
from ralph.pipeline import effect_executor as _effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.project_policy import _auto_commit as policy_auto_commit
from ralph.project_policy import agents_md as policy_agents_md
from ralph.project_policy import markers as policy_markers
from ralph.project_policy import remediation as policy_remediation
from ralph.project_policy.preflight import run_policy_readiness_preflight
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.cli.commands._load_result import _LoadResult
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.language_detector.models import ProjectStack
    from ralph.pipeline.factory import PipelineDeps
    from ralph.project_policy.models import ReadinessResult
    from ralph.project_policy.remediation import _InvokeRemediationAgent
    from ralph.workspace.protocol import Workspace
    from ralph.workspace.scope import WorkspaceScope


#: Process-level success exit code.
_EXIT_SUCCESS: int = 0

#: Process-level preflight-blocked exit code. Kept in sync with the
#: constant of the same name in :mod:`ralph.cli.commands.run`.
_EXIT_PREFLIGHT: int = 2


EmitFn = Callable[[str], None]

#: Explanation shown (via the info panel) before the skip-inline-policy
#: question. The wording is a contract: the user must understand that the
#: repo may already have its own policy, that Ralph's managed policy is a
#: good default if they are not confident in the existing setup, and what
#: each answer does to AGENTS.md.
_SKIP_PROMPT_EXPLANATION: str = (
    "AGENTS.md already contains project instructions — this repo may "
    "already have its own agent policy.\n\n"
    "  • Keep your existing policy: Ralph won't touch AGENTS.md and will "
    "skip policy enforcement (writes an opt-out marker).\n"
    "  • Use Ralph's managed policy: a good default if you're not "
    "confident in the existing setup — appends a managed block; your "
    "content is preserved byte-for-byte."
)

#: The yes/no question. Yes (default) appends the managed block, keeping
#: today's behavior; No writes the opt-out marker.
_SKIP_PROMPT_QUESTION: str = "Add Ralph's managed policy block to AGENTS.md?"


def _default_is_tty() -> bool:
    """Return True only when both stdin and stdout are real TTYs."""
    try:
        stdin_tty: bool = sys.stdin.isatty()
        stdout_tty: bool = sys.stdout.isatty()
    except Exception:  # pragma: no cover - defensive
        return False
    return stdin_tty and stdout_tty


def _default_confirm(question: str) -> bool:
    """Production confirm: typer prompt defaulting to Yes (append block)."""
    return bool(typer.confirm(question, default=True))


def _maybe_offer_inline_policy_skip(
    workspace: Workspace,
    emit: EmitFn,
    *,
    confirm: Callable[[str], bool] | None,
    is_tty: Callable[[], bool] | None,
) -> None:
    """Offer to skip the inline policy when AGENTS.md is significant.

    Fires only on first contact: a marker-free AGENTS.md with significant
    user content (see
    :func:`ralph.project_policy.agents_md.has_significant_unmanaged_content`)
    AND an interactive terminal. Declining persists the byte-exact opt-out
    marker so the preflight takes its SKIPPED path now and on every future
    run; accepting changes nothing (the bootstrap appends the managed block
    as before). Either answer therefore makes the offer one-time.

    A crashing prompt (EOF despite isatty, broken pipe) is swallowed and
    the run proceeds with today's default behavior — interactivity must
    never block or crash a run.
    """
    tty_check = is_tty if is_tty is not None else _default_is_tty
    if not tty_check():
        # Cheap check first: unattended runs (the common case) skip the
        # AGENTS.md read entirely.
        return
    if not policy_agents_md.has_significant_unmanaged_content(workspace):
        return
    confirm_fn = confirm if confirm is not None else _default_confirm
    emit(_SKIP_PROMPT_EXPLANATION)
    try:
        add_block = confirm_fn(_SKIP_PROMPT_QUESTION)
    except Exception as exc:
        logger.debug("skip-inline-policy prompt failed (non-fatal): {}", exc)
        emit(
            "Prompt unavailable — proceeding with the default: Ralph's "
            "managed policy block will be added to AGENTS.md."
        )
        return
    if add_block:
        return
    policy_agents_md.write_opt_out(workspace)
    emit(
        "Keeping the existing AGENTS.md policy — wrote the opt-out marker; "
        "Ralph policy enforcement is disabled for this repository."
    )


def _maybe_resolve_schema_upgrade(
    workspace: Workspace,
    emit: EmitFn,
    *,
    confirm: Callable[[str], bool] | None,
    is_tty: Callable[[], bool] | None,
) -> bool:
    """Offer a single all-or-nothing upgrade-or-freeze choice for older copies.

    When one or more customized policy files carry an older (but valid)
    schema marker, the user is asked exactly ONCE — not once per file.
    Accepting upgrades every listed file through the remediation agent;
    declining freezes every file at its current schema (writing a
    ``freeze vN`` marker) and emits guidance on how to remove the skip
    later. Non-interactive runs return ``False`` (the run is blocked until
    the choice is made interactively) and a malformed / future schema
    marker fails closed.
    """
    paths = [
        f"{policy_markers.CANONICAL_DIR}{name}"
        for name in (
            *policy_markers.CORE_POLICY_FILES,
            *policy_markers.CONDITIONAL_POLICY_FILES.values(),
        )
        if workspace.exists(f"{policy_markers.CANONICAL_DIR}{name}")
    ]
    outdated: list[tuple[str, str, int]] = []
    invalid_schema = False
    current_version = int(policy_markers.SCHEMA_VERSION.removeprefix("v"))
    for path in paths:
        lines = workspace.read(path).splitlines()
        first_line = next((line for line in lines if line.strip()), "")
        if first_line == policy_markers.POLICY_SCHEMA_MARKER:
            continue
        freeze_match = re.fullmatch(
            r"<!-- ralph-policy-schema: freeze v([0-9]+) -->", first_line
        )
        if freeze_match is not None:
            frozen_version = int(freeze_match.group(1))
            if frozen_version < current_version:
                continue
            emit(
                f"Policy {path} has invalid freeze schema v{frozen_version}; "
                f"a freeze must be older than {policy_markers.SCHEMA_VERSION}."
            )
            invalid_schema = True
            break
        match = re.fullmatch(r"<!-- ralph-policy-schema: v([0-9]+) -->", first_line)
        if match is None:
            emit(f"Policy schema marker is missing or malformed in {path}.")
            invalid_schema = True
            break
        installed_version = int(match.group(1))
        if installed_version > current_version:
            emit(
                f"Policy {path} uses future schema v{installed_version}; "
                f"this Ralph version supports {policy_markers.SCHEMA_VERSION}."
            )
            invalid_schema = True
            break
        outdated.append((path, first_line, installed_version))
    if invalid_schema:
        return False
    if not outdated:
        return True
    tty_check = is_tty if is_tty is not None else _default_is_tty
    if not tty_check():
        emit(
            "Policy schema choice required; rerun interactively to upgrade "
            "or freeze the customized policy file(s)."
        )
        return False
    file_list = "\n".join(f"  • {path}" for path, _marker, _v in outdated)
    emit(
        f"A newer Ralph policy schema ({policy_markers.SCHEMA_VERSION}) is available "
        f"for {len(outdated)} customized policy file(s):\n{file_list}"
    )
    confirm_fn = confirm if confirm is not None else _default_confirm
    frozen: list[str] = []
    try:
        # One all-or-nothing choice, never one prompt per file: Yes upgrades
        # every listed file through the remediation agent; No freezes every
        # file at its current schema so none is touched.
        upgrade_all = confirm_fn(
            f"Upgrade all {len(outdated)} file(s) to {policy_markers.SCHEMA_VERSION} "
            "through the remediation agent? (Declining freezes every file at "
            "its current schema.)"
        )
        if not upgrade_all:
            for path, marker, installed_version in outdated:
                content = workspace.read(path)
                workspace.write(
                    path,
                    content.replace(
                        marker,
                        f"<!-- ralph-policy-schema: freeze v{installed_version} -->",
                        1,
                    ),
                )
                frozen.append(path)
    except Exception as exc:
        logger.debug("policy-schema prompt failed: {}", exc)
        emit("Policy schema choice could not be completed; no implicit upgrade was applied.")
        return False
    if upgrade_all:
        return True
    frozen_list = "\n".join(f"  • {path}" for path in frozen)
    emit(
        f"Froze {len(frozen)} policy file(s) at their current schema — Ralph "
        f"will not upgrade them:\n{frozen_list}\n\n"
        "Changed your mind? Remove the skip: delete the "
        "`<!-- ralph-policy-schema: freeze vN -->` line at the top of the file "
        "(or change `freeze vN` back to `vN`) and rerun — Ralph will offer the "
        "upgrade again."
    )
    return True


def _resolve_remediation_chain_agents(load_result: _LoadResult) -> list[str]:
    """Return the fallback agents of the chain bound to the remediation drain.

    Resolution reuses :class:`ralph.agents.chain.ChainManager` — the exact
    strict drain->chain lookup the pipeline uses — so the out-of-graph
    remediation path cannot drift from pipeline routing. The loader may
    alias the ``policy_remediation`` drain to the user's development chain.
    Returns an empty list when the bundle is missing or the drain does not
    resolve to a non-empty chain.
    """
    bundle = load_result.policy_bundle
    if bundle is None:
        return []
    try:
        chain = ChainManager(bundle.agents).chain_for_drain("policy_remediation")
    except (DrainNotBoundError, ValueError):
        return []
    return [agent.strip() for agent in chain.agents if agent.strip()]


def _build_pipeline_deps_for_remediation(
    load_result: _LoadResult,
    display_context: DisplayContext,
) -> PipelineDeps | None:
    """Build ``PipelineDeps`` for the synchronous remediation driver.

    Returns ``None`` when the bundle is missing or factory construction
    fails (defensive: a missing deps block simply prevents the production
    agent invocation from running, but tests inject a fake and pass).
    """
    if load_result.policy_bundle is None:
        return None
    try:
        return DefaultPipelineFactory().build(
            load_result.config,
            display_context,
            model_identity=None,
            policy_bundle=load_result.policy_bundle,
            pro_hooks=None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not build pipeline deps for remediation: {}", exc)
        return None


def _make_production_invoke_remediation_agent(
    load_result: _LoadResult,
    pipeline_deps: PipelineDeps | None,
    workspace_scope: WorkspaceScope,
    chain_agents: list[str],
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
) -> _InvokeRemediationAgent:
    """Build the production ``invoke_remediation_agent`` closure.

    One call walks the resolved fallback chain in order — the same
    semantics a pipeline drain gets — invoking each agent through
    :func:`execute_agent_effect` until one succeeds. A chain that ran and
    failed returns ``False`` (the driver retries within its budget); a
    launch crash raises :class:`RemediationInvocationError` so the driver
    aborts instead of spinning through the budget.
    """

    def invoke_remediation_agent(prompt_path: str) -> bool:
        if pipeline_deps is None or load_result.policy_bundle is None:
            return False
        for agent_name in chain_agents:
            effect = InvokeAgentEffect(
                agent_name=agent_name,
                phase="policy_remediation",
                prompt_file=prompt_path,
                drain="policy_remediation",
                chain_name="policy_remediation",
            )
            try:
                event = _effect_executor_module.execute_agent_effect(
                    effect,
                    load_result.config,
                    pipeline_deps,
                    workspace_scope,
                    run_id=load_result.run_id,
                    policy_bundle=load_result.policy_bundle,
                    display=display,
                    display_context=display_context,
                )
            except Exception as exc:
                logger.warning("Remediation agent invocation failed: {}", exc)
                raise policy_remediation.RemediationInvocationError(str(exc)) from exc
            if event == PipelineEvent.AGENT_SUCCESS:
                return True
        return False

    typed: _InvokeRemediationAgent = invoke_remediation_agent
    return typed


def _resolve_max_attempts(load_result: _LoadResult) -> int:
    """Return the remediation attempt budget.

    Always the remediation driver's own small budget. The global recovery
    ``cycle_cap`` (default 200) governs pipeline recovery cycles and must
    NOT leak in here: 200 synchronous (agent, revalidate) rounds at startup
    is a display flood, not a remediation strategy.
    """
    del load_result
    return policy_remediation.DEFAULT_MAX_ATTEMPTS


def _build_workspace(
    load_result: _LoadResult,
    workspace_factory: Callable[[], Workspace] | None,
) -> Workspace:
    """Return the workspace, using the injected factory when available."""
    if workspace_factory is not None:
        return workspace_factory()
    scope = load_result.workspace_scope
    if scope is None:
        msg = "_build_workspace called with a missing workspace_scope"
        raise RuntimeError(msg)
    return FsWorkspace(scope.root, allowed_roots=scope.allowed_roots)


def _push_remediation_status_bar(
    display: object,
    workspace_scope: WorkspaceScope,
    max_attempts: int,
) -> None:
    """Seed the persistent status bar for the remediation phase.

    Mirrors the run loop's phase push so the footer shows the working
    directory and the active phase during remediation instead of nothing.
    Defensive: any display failure is swallowed — presentation must never
    block remediation.
    """
    try:
        model = StatusBarModel(
            workspace_root=str(workspace_scope.root),
            phase_label="Policy Remediation",
            phase_style=phase_style_for_phase("policy_remediation"),
            outer_dev_iteration=1,
            outer_dev_cap=max_attempts,
        )
        update = cast(
            "Callable[[object], None] | None",
            getattr(display, "update_status_bar", None),
        )
        if update is not None:
            update(model)
    except Exception as exc:
        logger.debug("remediation status-bar push failed (non-fatal): {}", exc)


def _finalize_ready_state(workspace: Workspace, workspace_scope: WorkspaceScope) -> None:
    """Post-READY housekeeping: condense the temporary AGENTS.md placeholder
    block to its concise form, then auto-commit the policy surfaces."""
    try:
        policy_agents_md.condense_placeholder_block(workspace)
    except Exception as exc:
        logger.debug("AGENTS.md placeholder condense failed (non-fatal): {}", exc)
    _auto_commit_policy_changes(workspace_scope)


def _auto_commit_policy_changes(workspace_scope: WorkspaceScope) -> None:
    """Best-effort auto-commit of the policy surfaces after READY.

    Mirrors the wt-025 skill auto-commit so the next run's development
    agent never sees readiness drift in its working tree. Failures are
    logged and swallowed — a broken git state must not block the run.
    """
    try:
        sha = policy_auto_commit.commit_policy_updates(workspace_scope.root, create_commit)
        if sha is not None:
            logger.debug("project-policy auto-commit created: {}", sha)
    except Exception as exc:
        logger.debug("project-policy auto-commit failed (non-fatal): {}", exc)


def _build_emit(
    display_context: DisplayContext,
    emit_factory: Callable[[str], None] | None,
) -> Callable[[str], None]:
    """Return the display emit, using the injected callback when available."""
    if emit_factory is not None:
        return emit_factory

    def emit(message: str) -> None:
        display = resolve_active_display(None, display_context)
        display.emit_info_panel(
            title="Project-Policy Readiness",
            content=message,
        )

    return emit


def _dispatch_preflight_result(
    *,
    load_result: _LoadResult,
    display_context: DisplayContext,
    result: ReadinessResult,
    workspace_scope: WorkspaceScope,
    workspace: Workspace,
    stack: ProjectStack,
    emit: Callable[[str], None],
    invoke_remediation_agent_factory: Callable[[Workspace], Callable[[str], bool]]
    | None,
) -> int:
    """Map a :class:`ReadinessResult` to a CLI exit code.

    Extracted from :func:`run_project_policy_readiness` so the
    orchestrator stays under PLR0911 while the dispatch logic keeps its
    explicit state-machine branches.
    """
    if not result.requires_remediation() and not result.is_blocked():
        return _EXIT_PREFLIGHT

    chain_agents = _resolve_remediation_chain_agents(load_result)
    if not chain_agents and invoke_remediation_agent_factory is None:
        logger.warning(
            "policy_remediation chain has no usable configured agent; "
            "blocking the run."
        )
        emit(
            "Project-policy-readiness: BLOCKED \u2014 policy_remediation chain "
            "has no configured agent."
        )
        return _EXIT_PREFLIGHT

    pipeline_deps = _build_pipeline_deps_for_remediation(load_result, display_context)
    display = resolve_active_display(None, display_context)
    if invoke_remediation_agent_factory is not None:
        invoke_remediation_agent: _InvokeRemediationAgent = cast(
            "_InvokeRemediationAgent",
            invoke_remediation_agent_factory(workspace),
        )
    else:
        invoke_remediation_agent = _make_production_invoke_remediation_agent(
            load_result,
            pipeline_deps,
            workspace_scope,
            chain_agents,
            display,
            display_context,
        )

    max_attempts = _resolve_max_attempts(load_result)
    # Drive the SAME display lifecycle the pipeline run loop uses: a
    # started display (live status bar) for the duration of the agent
    # work, with a status-bar model naming the remediation phase.
    with display:
        _push_remediation_status_bar(display, workspace_scope, max_attempts)
        final = policy_remediation.remediate(
            workspace,
            stack,
            result.findings,
            invoke_remediation_agent=invoke_remediation_agent,
            max_attempts=max_attempts,
            emit=emit,
        )
    if final.is_ready():
        _finalize_ready_state(workspace, workspace_scope)
        return _EXIT_SUCCESS
    report_lines = ["Project-policy-readiness: BLOCKED"]
    report_lines.extend(final.report_lines)
    emit("\n".join(report_lines))
    return _EXIT_PREFLIGHT


def run_project_policy_readiness(
    *,
    load_result: _LoadResult,
    display_context: DisplayContext,
    workspace_factory: Callable[[], Workspace] | None = None,
    emit_factory: Callable[[str], None] | None = None,
    invoke_remediation_agent_factory: Callable[[Workspace], Callable[[str], bool]]
    | None = None,
    confirm_factory: Callable[[str], bool] | None = None,
    is_tty: Callable[[], bool] | None = None,
) -> int:
    """Run the project-policy-readiness preflight at run_pipeline startup.

    Steps:

    #. Build the workspace + project stack via the injected seams.
    #. On first contact with a significant, marker-free AGENTS.md and an
       interactive terminal, offer to keep the existing policy instead of
       adding Ralph's managed block (see
       :func:`_maybe_offer_inline_policy_skip`).
    #. Call :func:`ralph.project_policy.run_policy_readiness_preflight`.
    #. Map the result status to a CLI exit code: ``READY`` and ``SKIPPED``
       continue. ``REMEDIATION_REQUIRED`` triggers an in-process bounded
       remediation loop. ``BLOCKED`` returns the recoverable
       ``_EXIT_PREFLIGHT`` exit.

    Tests can inject ``workspace_factory``, ``emit_factory``,
    ``invoke_remediation_agent_factory``, ``confirm_factory``, and ``is_tty`` to
    exercise the preflight without real filesystem I/O, agent invocation,
    or a real terminal.
    """
    workspace_scope = load_result.workspace_scope
    if workspace_scope is None:
        return _EXIT_SUCCESS

    emit = _build_emit(display_context, emit_factory)
    workspace = _build_workspace(load_result, workspace_factory)
    _maybe_offer_inline_policy_skip(
        workspace, emit, confirm=confirm_factory, is_tty=is_tty
    )
    if not _maybe_resolve_schema_upgrade(
        workspace, emit, confirm=confirm_factory, is_tty=is_tty
    ):
        return _EXIT_PREFLIGHT
    stack = get_project_stack(workspace)
    result = run_policy_readiness_preflight(workspace, stack, emit=emit)

    if result.is_skipped():
        emit("project-policy-readiness: skipped (opt-out marker present)")
        return _EXIT_SUCCESS

    if result.is_ready():
        emit(
            f"project-policy-readiness: ready "
            f"({len(result.changed_files)} files updated)"
        )
        _finalize_ready_state(workspace, workspace_scope)
        return _EXIT_SUCCESS

    return _dispatch_preflight_result(
        load_result=load_result,
        display_context=display_context,
        result=result,
        workspace_scope=workspace_scope,
        workspace=workspace,
        stack=stack,
        emit=emit,
        invoke_remediation_agent_factory=invoke_remediation_agent_factory,
    )


__all__ = [
    "_EXIT_PREFLIGHT",
    "_EXIT_SUCCESS",
    "EmitFn",
    "run_project_policy_readiness",
]
