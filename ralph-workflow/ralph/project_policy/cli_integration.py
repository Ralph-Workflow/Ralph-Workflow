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
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.chain import ChainManager, DrainNotBoundError
from ralph.display.parallel_display import phase_style_for_phase, resolve_active_display
from ralph.display.status_bar import StatusBarModel
from ralph.git.operations import create_commit
from ralph.git.scoped_auto_commit import list_dirty_paths
from ralph.language_detector import get_project_stack
from ralph.pipeline import effect_executor as _effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.project_policy import _auto_commit as policy_auto_commit
from ralph.project_policy import _prompt_ui
from ralph.project_policy import agents_md as policy_agents_md
from ralph.project_policy import evidence as policy_evidence
from ralph.project_policy import markers as policy_markers
from ralph.project_policy import remediation as policy_remediation
from ralph.project_policy.pipeline_driver import run_policy_pipeline
from ralph.project_policy.pipeline_graph import (
    DEFAULT_ANALYSIS_CAP,
    PHASE_ANALYSIS,
    PHASE_REMEDIATION,
)
from ralph.project_policy.policy_mode import PolicyMode
from ralph.project_policy.preflight import run_policy_readiness_preflight
from ralph.project_policy.reset import reset_policy_state
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.cli.commands._load_result import _LoadResult
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.language_detector.models import ProjectStack
    from ralph.pipeline.factory import PipelineDeps
    from ralph.project_policy.analysis import InvokePolicyAgent
    from ralph.project_policy.models import ReadinessResult
    from ralph.workspace.protocol import Workspace
    from ralph.workspace.scope import WorkspaceScope


#: Process-level success exit code.
_EXIT_SUCCESS: int = 0

#: Process-level preflight-blocked exit code. Kept in sync with the
#: constant of the same name in :mod:`ralph.cli.commands.run`.
_EXIT_PREFLIGHT: int = 2


EmitFn = Callable[[str], None]

#: Estimated wall-clock cost of the one-time policy setup. Stated in one
#: place because it appears in several strings; it is an estimate, not a
#: measurement, and projects with more surface area take longer.
_SETUP_ESTIMATE: str = "roughly 30 minutes"

#: A menu whose "explain" item re-asks could in principle loop forever
#: against a broken seam. Bound it; exhausting the rounds falls through to
#: the menu's default answer.
_MAX_PROMPT_ROUNDS: int = 8

#: Stable choice keys. Control flow branches on these, never on the copy.
_CHOICE_ADOPT: str = "adopt"
_CHOICE_KEEP: str = "keep"
_CHOICE_EXPLAIN: str = "explain"
_CHOICE_UPGRADE: str = "upgrade"
_CHOICE_FREEZE: str = "freeze"

#: Explanation shown (via the info panel) before the adopt-or-keep menu.
#: The wording is a contract: the user must understand that the repo may
#: already have its own policy, what adopting actually costs and produces,
#: that the resulting policy files help in any AI coding session (not only
#: Ralph Workflow runs), that their own content is never rewritten, and
#: which choice suits which kind of user.
#:
#: The per-choice consequences live HERE rather than in the menu's own
#: descriptions because questionary renders a description as a single line
#: and clips it at the terminal width. The panel is wrapped by the display,
#: so it is the only place long-form guidance survives intact.
_INIT_PANEL: str = (
    "AGENTS.md already contains agent instructions, so this repository may "
    "already have a process of its own.\n\n"
    "Adopting Ralph Workflow's managed policy is a ONE-TIME setup that runs "
    "before your first task. Ralph Workflow seeds the core policy files under "
    f"{policy_markers.CANONICAL_DIR} — testing, type checking, linting, "
    "dependencies, verification, agents, clean code, documentation, security, "
    "architecture — plus any that apply to your stack, then an agent fills "
    "each one in against your actual codebase. Expect "
    f"{_SETUP_ESTIMATE} of agent work and a meaningful token spend.\n\n"
    "The result is plain markdown checked into your repository. It guides any "
    "AI coding assistant that reads AGENTS.md — Claude Code, Cursor, and the "
    "rest — not just Ralph Workflow runs.\n\n"
    "Your choices:\n\n"
    "  • Adopt Ralph Workflow's managed policy. Pick this if you are not an "
    "experienced software developer, or if you are not confident that your "
    "current process already covers testing, review, and verification. Your "
    "existing AGENTS.md content is preserved byte-for-byte — Ralph Workflow "
    "only appends a managed block.\n"
    "  • Keep my existing policy. Pick this if you already have a strong "
    "engineering process in place and know it holds. AGENTS.md is left "
    "untouched, an opt-out marker is written, and policy enforcement stays "
    "off for this repository."
)

_INIT_QUESTION: str = "What should Ralph Workflow do about this repository's policy?"

# Menu descriptions are one clipped line each; keep them short. The full
# consequences are in _INIT_PANEL above.
_INIT_CHOICES: tuple[_prompt_ui.PromptChoice, ...] = (
    _prompt_ui.PromptChoice(
        key=_CHOICE_ADOPT,
        title="Adopt Ralph Workflow's managed policy "
        f"(one-time setup, {_SETUP_ESTIMATE})",
        description="Best choice if you are not an experienced developer.",
    ),
    _prompt_ui.PromptChoice(
        key=_CHOICE_KEEP,
        title="Keep my existing policy (no setup, no enforcement)",
        description="For teams whose engineering process is already strong.",
    ),
    _prompt_ui.PromptChoice(
        key=_CHOICE_EXPLAIN,
        title="What exactly does Ralph Workflow's policy contain?",
        description="Lists the files that would be created. Writes nothing.",
    ),
)

_SCHEMA_QUESTION: str = "What should Ralph Workflow do with these policy files?"


def _default_is_tty() -> bool:
    """Return True only when both stdin and stdout are real TTYs."""
    try:
        stdin_tty: bool = sys.stdin.isatty()
        stdout_tty: bool = sys.stdout.isatty()
    except Exception:  # pragma: no cover - defensive
        return False
    return stdin_tty and stdout_tty


def _ask(
    select: _prompt_ui.SelectFn,
    emit: EmitFn,
    question: str,
    choices: Sequence[_prompt_ui.PromptChoice],
    default: str,
    *,
    fallback_notice: str,
) -> str:
    """Ask one menu, returning ``default`` if the seam itself blows up.

    :func:`ralph.project_policy._prompt_ui.select` already absorbs its own
    failures, so this guard exists for an injected seam and for the
    contract it protects: an unusable prompt must never block or crash a
    run, it must fall through to the documented default.
    """
    try:
        return select(question, choices, default)
    except Exception as exc:
        logger.debug("policy prompt failed (non-fatal): {}", exc)
        emit(fallback_notice)
        return default


def _policy_contents_detail(workspace: Workspace) -> str:
    """Render the exact policy files this project would get, and why."""
    core = "\n".join(
        f"  • {policy_markers.CANONICAL_DIR}{name}"
        for name in policy_markers.CORE_POLICY_FILES
    )
    stack = get_project_stack(workspace)
    requirements = policy_evidence.conditional_domain_requirements(workspace, stack)
    conditional = [
        f"  • {policy_markers.CANONICAL_DIR}{name}"
        for domain, name in policy_markers.CONDITIONAL_POLICY_FILES.items()
        if requirements[domain][0]
    ]
    detail = (
        "Ralph Workflow would create these core policy files, one per "
        f"quality domain every software project needs:\n\n{core}\n"
    )
    if conditional:
        detail += (
            "\nPlus these, because this project's code shows it needs "
            "them:\n\n" + "\n".join(conditional) + "\n"
        )
    return (
        detail + "\nEach file starts as a template and is filled in against "
        "your codebase: your real build, test, and lint commands, your "
        "frameworks, your exceptions. That authoring pass is the "
        f"{_SETUP_ESTIMATE} of one-time agent work."
    )


def _maybe_offer_inline_policy_skip(
    workspace: Workspace,
    emit: EmitFn,
    *,
    select: _prompt_ui.SelectFn | None,
    is_tty: Callable[[], bool] | None,
) -> None:
    """Offer to keep the existing policy when AGENTS.md is significant.

    Fires only on first contact: a marker-free AGENTS.md with significant
    user content (see
    :func:`ralph.project_policy.agents_md.has_significant_unmanaged_content`)
    AND an interactive terminal. Choosing "keep" persists the byte-exact
    opt-out marker so the preflight takes its SKIPPED path now and on every
    future run; choosing "adopt" changes nothing (the bootstrap appends the
    managed block as before). Either answer therefore makes the offer
    one-time. The third choice explains what the policy contains and
    re-asks without writing anything.

    A prompt that cannot run (EOF despite isatty, broken pipe, Ctrl-C) is
    swallowed and the run proceeds on the default — adopt — which is
    today's behavior.
    """
    tty_check = is_tty if is_tty is not None else _default_is_tty
    if not tty_check():
        # Cheap check first: unattended runs (the common case) skip the
        # AGENTS.md read entirely.
        return
    if not policy_agents_md.has_significant_unmanaged_content(workspace):
        return
    select_fn = select if select is not None else _prompt_ui.select
    emit(_INIT_PANEL)
    for _round in range(_MAX_PROMPT_ROUNDS):
        choice = _ask(
            select_fn,
            emit,
            _INIT_QUESTION,
            _INIT_CHOICES,
            _CHOICE_ADOPT,
            fallback_notice=(
                "Prompt unavailable — proceeding with the default: Ralph "
                "Workflow's managed policy block will be added to AGENTS.md."
            ),
        )
        if choice == _CHOICE_EXPLAIN:
            emit(_policy_contents_detail(workspace))
            continue
        if choice == _CHOICE_KEEP:
            policy_agents_md.write_opt_out(workspace)
            emit(
                "Keeping the existing AGENTS.md policy — wrote the opt-out "
                "marker; Ralph Workflow policy enforcement is disabled for "
                "this repository."
            )
        return


def _schema_choices(count: int) -> tuple[_prompt_ui.PromptChoice, ...]:
    """Build the upgrade-or-freeze menu for ``count`` outdated policy files."""
    files = "file" if count == 1 else "files"
    return (
        _prompt_ui.PromptChoice(
            key=_CHOICE_UPGRADE,
            title=f"Upgrade all {count} {files} to {policy_markers.SCHEMA_VERSION}",
            description="An agent rewrites them; your rules carry across.",
        ),
        _prompt_ui.PromptChoice(
            key=_CHOICE_FREEZE,
            title=f"Keep all {count} {files} on their current schema",
            description="Left as they are. Ralph Workflow will not re-ask.",
        ),
        _prompt_ui.PromptChoice(
            key=_CHOICE_EXPLAIN,
            title="What does upgrading involve?",
            description="Explains both choices, then asks again.",
        ),
    )


#: What an upgrade mechanically does. Deliberately describes the process,
#: not a feature list for the new schema version: the schema constant is
#: bumped without a per-version changelog, so a feature list here would go
#: stale (or be invented) on the next bump.
_SCHEMA_EXPLAIN: str = (
    "The schema is the structure Ralph Workflow's deterministic validator "
    "reads: the markers, the declared gate commands, and the placeholders "
    "that must be resolved before a policy counts as complete. When the "
    "schema moves, files written against the old one can no longer be fully "
    "validated.\n\n"
    "Upgrading hands each file to an agent, which rewrites it into the "
    "current structure and carries your project-specific rules across. You "
    "review the result like any other change — it lands in your working "
    "tree.\n\n"
    "Freezing leaves the files exactly as they are. They keep working, but "
    "Ralph Workflow stops offering to bring them forward, and later versions "
    "will validate less of them."
)


def _maybe_resolve_schema_upgrade(
    workspace: Workspace,
    emit: EmitFn,
    *,
    select: _prompt_ui.SelectFn | None,
    is_tty: Callable[[], bool] | None,
) -> bool:
    """Offer a single all-or-nothing upgrade-or-freeze choice for older copies.

    When one or more customized policy files carry an older (but valid)
    schema marker, the user is asked exactly ONCE — not once per file.
    Upgrading rewrites every listed file through the remediation agent;
    freezing pins every file at its current schema (writing a ``freeze vN``
    marker) and emits guidance on how to remove the skip later. A third
    choice explains what an upgrade involves and re-asks. Non-interactive
    runs return ``False`` (the run is blocked until the choice is made
    interactively) and a malformed / future schema marker fails closed.
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
    file_list = "\n".join(
        f"  • {path}  (currently v{version})" for path, _marker, version in outdated
    )
    emit(
        f"Ralph Workflow's policy schema {policy_markers.SCHEMA_VERSION} is "
        f"available. {len(outdated)} policy file(s) you have customized are "
        f"still on an older schema:\n{file_list}\n\n"
        "Your choices:\n\n"
        "  • Upgrade them. An agent rewrites each file into the current "
        "schema, carrying your project-specific rules across, and the result "
        "lands in your working tree for you to review. This adds agent work "
        "to the start of this run.\n"
        "  • Keep them on their current schema. The files are frozen exactly "
        "as they are and Ralph Workflow stops offering to bring them forward. "
        "Reversible later by deleting the `freeze` line at the top of a file."
    )
    select_fn = select if select is not None else _prompt_ui.select
    choices = _schema_choices(len(outdated))
    for _round in range(_MAX_PROMPT_ROUNDS):
        # One all-or-nothing choice, never one prompt per file.
        choice = _ask(
            select_fn,
            emit,
            _SCHEMA_QUESTION,
            choices,
            _CHOICE_UPGRADE,
            fallback_notice=(
                "Policy schema choice could not be completed; no implicit "
                "upgrade was applied."
            ),
        )
        if choice == _CHOICE_EXPLAIN:
            emit(_SCHEMA_EXPLAIN)
            continue
        if choice == _CHOICE_UPGRADE:
            return True
        _freeze_policy_files(workspace, emit, outdated)
        return True
    return True


def _freeze_policy_files(
    workspace: Workspace,
    emit: EmitFn,
    outdated: Sequence[tuple[str, str, int]],
) -> None:
    """Pin every outdated policy file at its installed schema version."""
    frozen: list[str] = []
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
    frozen_list = "\n".join(f"  • {path}" for path in frozen)
    emit(
        f"Froze {len(frozen)} policy file(s) at their current schema — Ralph "
        f"Workflow will not upgrade them:\n{frozen_list}\n\n"
        "Changed your mind? Remove the skip: delete the "
        "`<!-- ralph-policy-schema: freeze vN -->` line at the top of the file "
        "(or change `freeze vN` back to `vN`) and rerun — Ralph Workflow will "
        "offer the upgrade again."
    )


def _resolve_chain_agents(load_result: _LoadResult, drain: str) -> list[str]:
    """Return the fallback agents of the chain bound to ``drain``.

    Resolution reuses :class:`ralph.agents.chain.ChainManager` — the exact
    strict drain->chain lookup the pipeline uses — so the out-of-graph policy
    phases cannot drift from pipeline routing. The loader may alias either policy
    drain to the user's corresponding chain. Returns an empty list when the
    bundle is missing or the drain does not resolve to a non-empty chain.
    """
    bundle = load_result.policy_bundle
    if bundle is None:
        return []
    try:
        chain = ChainManager(bundle.agents).chain_for_drain(drain)
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


def _make_production_invoke_agent(
    load_result: _LoadResult,
    pipeline_deps: PipelineDeps | None,
    workspace_scope: WorkspaceScope,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
) -> InvokePolicyAgent:
    """Build the production ``invoke_agent`` closure for BOTH policy phases.

    One call walks the resolved fallback chain of the phase's drain in order —
    the same semantics a pipeline drain gets — invoking each agent through
    :func:`execute_agent_effect` until one succeeds. A chain that ran and failed
    returns ``False`` (the driver loops within its budget); a launch crash raises
    :class:`RemediationInvocationError` so the driver stops looping rather than
    spinning through the budget in milliseconds.

    Both policy phases are named identically to their drains, so the phase name
    IS the drain name — there is no mapping table to keep in sync.
    """

    def invoke_agent(*, phase: str, prompt_path: str) -> bool:
        if pipeline_deps is None or load_result.policy_bundle is None:
            return False
        chain_agents = _resolve_chain_agents(load_result, phase)
        last_error: Exception | None = None
        for agent_name in chain_agents:
            effect = InvokeAgentEffect(
                agent_name=agent_name,
                phase=phase,
                prompt_file=prompt_path,
                drain=phase,
                chain_name=phase,
                # The REMEDIATION session is denied artifact.submit (so
                # declare_complete is not in its tool surface) and has no
                # artifact contract, leaving it no way to produce completion
                # evidence; demanding it would fail every clean exit on the
                # completion-enforcing transports. It is judged by the
                # deterministic validator that re-runs after it exits, which is
                # the only evidence that ever counted.
                #
                # The ANALYSIS session is the opposite: returning a decision
                # artifact is its entire purpose, so it has a real contract and
                # its completion evidence is required.
                requires_completion_evidence=(phase == PHASE_ANALYSIS),
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
                # A crash launching ONE agent is not a failure of the chain: try
                # the next fallback, exactly as a pipeline drain would. Only when
                # every agent in the chain has crashed is this real infrastructure
                # breakage worth reporting to the driver.
                logger.warning(
                    "Policy {} agent {} could not be launched: {}", phase, agent_name, exc
                )
                last_error = exc
                continue
            if event == PipelineEvent.AGENT_SUCCESS:
                return True
        if last_error is not None:
            raise policy_remediation.RemediationInvocationError(
                str(last_error)
            ) from last_error
        return False

    typed: InvokePolicyAgent = invoke_agent
    return typed


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
    *,
    attempt: int = 1,
) -> None:
    """Seed the persistent status bar for the remediation phase.

    Mirrors the run loop's phase push so the footer shows the working
    directory and the active phase during remediation instead of nothing.
    ``attempt`` is the 1-indexed live attempt (1 on the first try, 2 on
    the first re-try, etc.) so the bar surfaces real progress instead
    of a hardcoded ``Dev 1/N`` placeholder. Defensive: any display
    failure is swallowed -- presentation must never block remediation.
    """
    try:
        model = StatusBarModel(
            workspace_root=str(workspace_scope.root),
            phase_label="Policy Remediation",
            phase_style=phase_style_for_phase("policy_remediation"),
            outer_dev_iteration=attempt,
            outer_dev_cap=max_attempts,
            outer_label="Remediation",
        )
        update = cast(
            "Callable[[object], None] | None",
            getattr(display, "update_status_bar", None),
        )
        if update is not None:
            update(model)
    except Exception as exc:
        logger.debug("remediation status-bar push failed (non-fatal): {}", exc)


def _snapshot_working_tree(workspace_scope: WorkspaceScope) -> frozenset[str]:
    """Capture the currently-dirty paths. Never raises."""
    try:
        return list_dirty_paths(workspace_scope.root)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("working-tree snapshot failed (non-fatal): {}", exc)
        return frozenset()


def _track_authored_paths(
    invoke_agent: InvokePolicyAgent,
    workspace_scope: WorkspaceScope,
    authored: set[str],
) -> InvokePolicyAgent:
    """Wrap ``invoke_agent`` so it records what the REMEDIATION agent authored.

    Snapshots the working tree immediately before and after each REMEDIATION
    invocation and accumulates the difference. That difference is the gate scripts
    (and any other out-of-directory file) the agent wrote, which the deterministic
    auto-commit then picks up.

    The ANALYSIS phase is deliberately NOT tracked. It executes every declared gate
    as a probe, and probes drop build detritus into the tree -- ``.coverage``,
    ``coverage.xml``, ``*.tsbuildinfo``, stray caches. Those are side effects of
    READING the project, not authored content, and committing them would be wrong.
    Only the phase that can actually write is attributed.
    """

    def tracked(*, phase: str, prompt_path: str) -> bool:
        if phase != PHASE_REMEDIATION:
            return invoke_agent(phase=phase, prompt_path=prompt_path)
        before = _snapshot_working_tree(workspace_scope)
        try:
            return invoke_agent(phase=phase, prompt_path=prompt_path)
        finally:
            authored.update(_snapshot_working_tree(workspace_scope) - before)

    typed: InvokePolicyAgent = tracked
    return typed


def _finalize_ready_state(
    workspace: Workspace,
    workspace_scope: WorkspaceScope,
    pre_run_dirty: frozenset[str] | None = None,
    authored_paths: frozenset[str] | None = None,
) -> None:
    """Post-READY housekeeping: condense the temporary AGENTS.md placeholder
    block to its concise form, then auto-commit the policy surfaces."""
    try:
        policy_agents_md.condense_placeholder_block(workspace)
    except Exception as exc:
        logger.debug("AGENTS.md placeholder condense failed (non-fatal): {}", exc)
    _auto_commit_policy_changes(workspace_scope, pre_run_dirty, authored_paths)


def _auto_commit_policy_changes(
    workspace_scope: WorkspaceScope,
    pre_run_dirty: frozenset[str] | None = None,
    authored_paths: frozenset[str] | None = None,
) -> None:
    """Best-effort deterministic auto-commit of everything the policy run wrote.

    Covers the policy surfaces AND any gate script the remediation agent authored
    to wire up a declared gate -- a gate script left uncommitted would dirty the
    working tree for the next agent and trip the commit-cleanup phase.

    No commit agent is involved: the subject and body are fixed, and the file set
    is computed deterministically. ``pre_run_dirty`` is subtracted from everything
    so the user's in-progress edits are never swept in; ``authored_paths`` is what
    the remediation agent actually wrote. Failures are logged and swallowed -- a
    broken git state must not block the run.
    """
    try:
        sha = policy_auto_commit.commit_policy_updates(
            workspace_scope.root,
            create_commit,
            pre_run_dirty=pre_run_dirty,
            authored_paths=authored_paths,
        )
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
    mode: PolicyMode,
    emit: Callable[[str], None],
    invoke_remediation_agent_factory: Callable[[Workspace], InvokePolicyAgent] | None,
) -> int:
    """Run the policy pipeline and map its result to an exit code.

    NOTE the exit codes: this function returns ``_EXIT_SUCCESS`` for every policy
    outcome unless the mode is an ``_ONLY`` mode. A policy that could not be made
    ready is a warning, not a failure of the run. See
    :func:`_exit_code_for_not_ready`.
    """
    chain_agents = _resolve_chain_agents(load_result, PHASE_REMEDIATION)
    if not chain_agents and invoke_remediation_agent_factory is None:
        logger.warning(
            "policy_remediation chain has no usable configured agent; "
            "skipping policy and continuing the run."
        )
        emit(
            "project-policy-readiness: the policy_remediation chain has no "
            "configured agent; continuing without a ready policy."
        )
        return _exit_code_for_not_ready(mode)

    # Snapshot BEFORE any agent runs. The post-run difference is what attributes
    # a newly-written gate script to the policy agents rather than to the user.
    pre_run_dirty = _snapshot_working_tree(workspace_scope)

    pipeline_deps = _build_pipeline_deps_for_remediation(load_result, display_context)
    display = resolve_active_display(None, display_context)
    if invoke_remediation_agent_factory is not None:
        invoke_agent = invoke_remediation_agent_factory(workspace)
    else:
        invoke_agent = _make_production_invoke_agent(
            load_result,
            pipeline_deps,
            workspace_scope,
            display,
            display_context,
        )

    # Record what the REMEDIATION agent writes outside the canonical directory
    # (its gate scripts), so the deterministic auto-commit can pick them up.
    authored: set[str] = set()
    invoke_agent = _track_authored_paths(invoke_agent, workspace_scope, authored)

    # Drive the SAME display lifecycle the pipeline run loop uses: a started
    # display (live status bar) for the duration of the agent work. The
    # callback below updates the persistent status bar with the live attempt
    # before each remediation iteration so the footer shows
    # ``Remediation N/Max`` instead of a hardcoded ``Dev 1/N`` placeholder.
    with display:
        def _on_remediation_attempt(attempt: int) -> None:
            _push_remediation_status_bar(
                display, workspace_scope, DEFAULT_ANALYSIS_CAP, attempt=attempt
            )

        _push_remediation_status_bar(display, workspace_scope, DEFAULT_ANALYSIS_CAP)
        final = run_policy_pipeline(
            workspace,
            stack,
            result.findings,
            invoke_agent=invoke_agent,
            entry_phase=mode.entry_phase(),
            analysis_cap=DEFAULT_ANALYSIS_CAP,
            emit=emit,
            on_remediation_attempt=_on_remediation_attempt,
        )
    if final.is_ready():
        _finalize_ready_state(
            workspace, workspace_scope, pre_run_dirty, frozenset(authored)
        )
        return _EXIT_SUCCESS
    emit("\n".join(final.report_lines))
    return _exit_code_for_not_ready(mode)


def _exit_code_for_not_ready(mode: PolicyMode) -> int:
    """Return the exit code for a policy that could not be made ready.

    THE RULE: a normal run NEVER exits non-zero because of policy. Policy is
    documentation about the project; a project with imperfect documentation is
    still a project you can do work on, and coupling the two means a stale
    RALPH-LANG block for a language nobody uses can stop all development.

    The ``_ONLY`` modes are the sole exception, and only because they have no
    development run to proceed to -- their exit code is the only signal they can
    give a CI job.
    """
    if mode.exits_after():
        return _EXIT_PREFLIGHT
    return _EXIT_SUCCESS


def run_project_policy_readiness(
    *,
    load_result: _LoadResult,
    display_context: DisplayContext,
    mode: PolicyMode = PolicyMode.NORMAL,
    workspace_factory: Callable[[], Workspace] | None = None,
    emit_factory: Callable[[str], None] | None = None,
    invoke_remediation_agent_factory: Callable[[Workspace], InvokePolicyAgent]
    | None = None,
    select_factory: _prompt_ui.SelectFn | None = None,
    is_tty: Callable[[], bool] | None = None,
) -> int:
    """Run the project-policy preflight at run_pipeline startup. NEVER blocks.

    THIS FUNCTION IS A FAULT BOUNDARY. It catches every exception the policy
    subsystem can raise -- including bugs: an ``AttributeError`` from a bad
    refactor, a ``KeyError`` from a malformed policy file, an ``OSError`` from a
    broken filesystem, a template that fails to render. Whatever happens, the
    development run proceeds as if the policy pipeline had never existed.

    That is not defensive paranoia, it is the requirement. Project policy is
    documentation ABOUT the project; it is not a precondition for working on the
    project. A crash in the documentation subsystem must never cost a user their
    development run. The catch is deliberately broad (``Exception``) and
    deliberately not broader (``BaseException`` is NOT caught, so Ctrl-C and
    ``SystemExit`` still work).

    The one exception to "never blocks" is an ``_ONLY`` mode, which has no
    development run to proceed to and so reports failure through its exit code.

    Tests can inject ``workspace_factory``, ``emit_factory``,
    ``invoke_remediation_agent_factory``, ``select_factory``, and ``is_tty`` to
    exercise the preflight without real filesystem I/O, agent invocation, or a
    real terminal.
    """
    try:
        return _run_policy_readiness(
            load_result=load_result,
            display_context=display_context,
            mode=mode,
            workspace_factory=workspace_factory,
            emit_factory=emit_factory,
            invoke_remediation_agent_factory=invoke_remediation_agent_factory,
            select_factory=select_factory,
            is_tty=is_tty,
        )
    except Exception as exc:
        logger.opt(exception=True).warning(
            "project-policy preflight crashed ({}); continuing to the "
            "development run without a ready policy.",
            exc,
        )
        _emit_safely(
            display_context,
            emit_factory,
            "project-policy-readiness: the policy preflight failed unexpectedly "
            f"({type(exc).__name__}: {exc}). Continuing to the development run.",
        )
        return _exit_code_for_not_ready(mode)


def _emit_safely(
    display_context: DisplayContext,
    emit_factory: Callable[[str], None] | None,
    message: str,
) -> None:
    """Emit a message, swallowing any failure of the display itself.

    Called from the fault boundary, where the display may be exactly what broke.
    A crash in the crash handler would defeat the entire point.
    """
    try:
        _build_emit(display_context, emit_factory)(message)
    except Exception as exc:  # pragma: no cover - the display is already broken
        logger.debug("policy failure could not be displayed: {}", exc)


def _run_policy_readiness(
    *,
    load_result: _LoadResult,
    display_context: DisplayContext,
    mode: PolicyMode,
    workspace_factory: Callable[[], Workspace] | None,
    emit_factory: Callable[[str], None] | None,
    invoke_remediation_agent_factory: Callable[[Workspace], InvokePolicyAgent] | None,
    select_factory: _prompt_ui.SelectFn | None,
    is_tty: Callable[[], bool] | None,
) -> int:
    """The preflight body. Every exit path here is wrapped by the fault boundary.

    Steps:

    #. Build the workspace + project stack via the injected seams.
    #. For an explicit mode, reset the policy (``--redo-policy``) and/or bypass
       the READY cache and the opt-out marker.
    #. On first contact with a significant, marker-free AGENTS.md and an
       interactive terminal, offer to keep the existing policy instead of adding
       Ralph Workflow's managed block.
    #. Run the deterministic preflight, then the policy pipeline.
    """
    workspace_scope = load_result.workspace_scope
    if workspace_scope is None:
        return _EXIT_SUCCESS

    emit = _build_emit(display_context, emit_factory)
    workspace = _build_workspace(load_result, workspace_factory)

    if mode.resets_policy():
        changed = reset_policy_state(workspace)
        emit(
            f"project-policy-readiness: reset {len(changed)} policy path(s); "
            "regenerating from scratch"
        )

    if not mode.is_explicit():
        _maybe_offer_inline_policy_skip(
            workspace, emit, select=select_factory, is_tty=is_tty
        )
        if not _maybe_resolve_schema_upgrade(
            workspace, emit, select=select_factory, is_tty=is_tty
        ):
            # The user declined a schema upgrade. Not a failure of the run.
            return _EXIT_SUCCESS

    stack = get_project_stack(workspace)
    result = run_policy_readiness_preflight(workspace, stack, emit=emit)

    if result.is_skipped() and not mode.is_explicit():
        emit("project-policy-readiness: skipped (opt-out marker present)")
        return _EXIT_SUCCESS

    # An explicit mode bypasses the READY cache: a --run-policy-agents that
    # no-ops on a ready project would be useless, since auditing a policy that
    # LOOKS ready is the entire point of the flag.
    if result.is_ready() and not mode.is_explicit():
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
        mode=mode,
        emit=emit,
        invoke_remediation_agent_factory=invoke_remediation_agent_factory,
    )


__all__ = [
    "_EXIT_PREFLIGHT",
    "_EXIT_SUCCESS",
    "EmitFn",
    "PolicyMode",
    "run_project_policy_readiness",
]
