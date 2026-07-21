"""Dev-agent conflict resolver builder for the auto-integrate step.

Builds the production :data:`~ralph.pipeline.auto_integrate_resolve.ConflictResolver`
handed to :func:`ralph.pipeline.auto_integrate.auto_integrate_after_commit`:
a focused invocation of an agent bound to the ``development`` drain
whose only job is to rewrite the conflicted files of the in-progress
endpoint merge in place. The agent never runs a git command: Ralph
stages the previously-conflicted paths and creates the merge commit
deterministically after verifying every conflict marker is gone.
Requiring the agent to stage would be unimplementable — an agent
running under Ralph's own MCP exec policy is denied every git
invocation.

Every invocation is bounded on both axes (idle watchdog + wall-clock
ceiling) and the drain's agent chain is walked up to
:data:`_MAX_RESOLVER_AGENTS` deep, so neither a hung resolver nor a
single unavailable agent can leave the repository parked in a
merge-in-progress state for the rest of the run.

Fault-tolerance contract: every failure mode (no dev agent configured,
prompt write failure, agent invocation error, every chain candidate
exhausted) returns ``False`` so the integration step aborts the merge
and records a conflict instead of crashing the run. The builder itself
never raises.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.invoke import InvokeOptions, invoke_agent
from ralph.git.merge import unmerged_paths
from ralph.timeout_defaults import IDLE_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from typing import Protocol

    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.policy.models import PolicyBundle

    class _SupportsAgentLookup(Protocol):
        """Structural registry surface: ``get(name) -> AgentConfig | None``."""

        def get(self, name: str) -> AgentConfig | None: ...

#: Drain whose agent chain supplies the conflict-resolution agent. The
#: development agent (not the commit agent) owns conflict resolution:
#: it is the agent already trusted to edit source under verification.
_RESOLVER_DRAIN = "development"

#: Transient prompt file written into the conflicted repository for the
#: duration of the resolution invocation, then removed.
_PROMPT_FILENAME = "auto_integrate_conflict_prompt.md"

#: How many agents of the development chain may be tried on one
#: conflicted merge. Bounded because each attempt costs a full agent
#: invocation at a commit seam; two is enough to survive a single
#: unavailable or crashing agent without doubling that cost again.
_MAX_RESOLVER_AGENTS = 2

#: Fallback ceiling for one resolution invocation when the config does
#: not carry the key (partially-constructed configs in tests).
_DEFAULT_RESOLVE_TIMEOUT_SECONDS = 900.0

#: Fallback body used when the conflicted-path query returned nothing,
#: so the agent still has an actionable instruction.
_UNKNOWN_PATHS_BODY = (
    "The conflicted paths could not be listed. Search the repository\n"
    "for files containing `<<<<<<< ` conflict markers and resolve every\n"
    "one of them."
)

_PROMPT_TEMPLATE = """# Resolve merge conflicts (auto-integrate)

The repository at `{root}` has an IN-PROGRESS merge of the mainline
branch `{target}` into the current feature branch, stopped on
conflicts.

{conflicted_body}

Your ONLY job:

1. Open each conflicted file listed above.
2. Resolve every conflict-marker region thoughtfully, preserving the
   intent of BOTH the feature branch and `{target}`. Never delete one
   side blindly.
3. Remove every `<<<<<<<`, `=======` and `>>>>>>>` marker line, so the
   file is left as valid, consistent content.

Hard rules:

- DO NOT run any git command. Ralph stages the resolved files and
  creates the merge commit itself. You only edit file contents.
- Do not commit, merge, rebase, abort, switch branches or move any
  refs.
- Do not modify files that had no conflict, except as strictly
  required to make the resolved code consistent.
- A leftover conflict marker in any listed file will cause the whole
  resolution to be REJECTED and the merge aborted.
"""


def build_agent_conflict_resolver(
    *,
    policy_bundle: PolicyBundle,
    registry: _SupportsAgentLookup,
    display: ParallelDisplay,
    config: UnifiedConfig,
) -> ConflictResolver:
    """Build the dev-agent conflict resolver for the integration step.

    The returned callable matches
    :data:`~ralph.pipeline.auto_integrate_resolve.ConflictResolver`:
    it is invoked with ``(repo_root, target_branch)`` while the
    conflicted merge is in progress and returns True only when an
    agent invocation completed, meaning the conflicts should now be
    resolved (the caller still verifies deterministically).

    ``config`` supplies the two bounds applied to every invocation:
    ``general.auto_integrate_resolve_timeout_seconds`` (wall clock) and
    ``general.agent_idle_timeout_seconds`` (idle watchdog).
    """

    def _resolver(root: Path, target: str) -> bool:
        candidates = _resolver_agent_names(policy_bundle)[:_MAX_RESOLVER_AGENTS]
        if not candidates:
            logger.warning(
                "auto_integrate: no agent bound to drain '{}'; cannot resolve",
                _RESOLVER_DRAIN,
            )
            return False
        for agent_name in candidates:
            if _try_resolve_with(
                agent_name,
                root=root,
                target=target,
                registry=registry,
                display=display,
                config=config,
            ):
                return True
        logger.warning(
            "auto_integrate: every candidate of drain '{}' failed to resolve",
            _RESOLVER_DRAIN,
        )
        return False

    return _resolver


def _try_resolve_with(
    agent_name: str,
    *,
    root: Path,
    target: str,
    registry: _SupportsAgentLookup,
    display: ParallelDisplay,
    config: UnifiedConfig,
) -> bool:
    """Run one candidate agent against the conflicted merge; never raises.

    Returns True only when the invocation ran to completion. Any
    failure (agent absent from the registry, prompt write failure,
    invocation error or timeout) returns False so the caller can move
    on to the next candidate, and ultimately abort the merge.
    """
    agent_config = registry.get(agent_name)
    if agent_config is None:
        logger.warning(
            "auto_integrate: agent '{}' not in registry; trying the next candidate",
            agent_name,
        )
        return False
    _emit_warn(
        display,
        f"merge of {target} conflicted — invoking {agent_name} to resolve",
    )
    prompt_path = _write_prompt(root, target, unmerged_paths(root))
    if prompt_path is None:
        return False
    try:
        _drain_invocation(agent_config, prompt_path, root, config)
    except Exception as invoke_exc:
        logger.warning(
            "auto_integrate: conflict-resolution invocation by '{}' failed: {}",
            agent_name,
            invoke_exc,
        )
        _emit_warn(
            display,
            f"conflict resolution by {agent_name} failed: {invoke_exc}",
        )
        return False
    finally:
        with contextlib.suppress(OSError):
            prompt_path.unlink()
    _emit_warn(
        display,
        f"{agent_name} finished conflict resolution; verifying and committing the merge",
    )
    return True


def _resolver_agent_names(policy_bundle: PolicyBundle) -> tuple[str, ...]:
    """Full agent chain bound to the ``development`` drain, in order.

    Empty when the drain or its chain is missing. The caller tries the
    candidates in order so one unavailable agent does not end the
    resolution attempt with the merge still in progress.
    """
    drain_binding = policy_bundle.agents.agent_drains.get(_RESOLVER_DRAIN)
    if drain_binding is None:
        return ()
    chain_config = policy_bundle.agents.agent_chains.get(drain_binding.chain)
    if chain_config is None or not chain_config.agents:
        return ()
    return tuple(chain_config.agents)


def _write_prompt(
    root: Path, target: str, conflicted_paths: Sequence[str]
) -> Path | None:
    """Write the transient resolution prompt; None on failure.

    The conflicted paths are interpolated into the prompt so the agent
    never needs a git command to discover them.
    """
    prompt_path = root / ".agent" / _PROMPT_FILENAME
    try:
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(
            _PROMPT_TEMPLATE.format(
                root=root,
                target=target,
                conflicted_body=_conflicted_body(conflicted_paths),
            ),
            encoding="utf-8",
        )
    except OSError as write_exc:
        logger.warning(
            "auto_integrate: failed to write conflict prompt: {}", write_exc
        )
        return None
    return prompt_path


def _conflicted_body(conflicted_paths: Sequence[str]) -> str:
    """Render the conflicted-path list, or the search fallback."""
    listed = [path for path in conflicted_paths if path.strip()]
    if not listed:
        return _UNKNOWN_PATHS_BODY
    bullets = "\n".join(f"- `{path}`" for path in listed)
    return f"Conflicted paths:\n\n{bullets}"


def _drain_invocation(
    agent_config: AgentConfig,
    prompt_path: Path,
    root: Path,
    config: UnifiedConfig,
) -> None:
    """Run the agent invocation to completion, discarding output lines.

    Both timeouts are passed explicitly.
    :mod:`ralph.agents.invoke._options` assigns
    ``idle_timeout_seconds=opts.idle_timeout_seconds`` with NO fallback
    to the config-derived base (unlike ``max_session_seconds``), so
    leaving it unset here would run this invocation with the idle
    watchdog disabled entirely.
    """
    idle_raw: object = getattr(config.general, "agent_idle_timeout_seconds", None)
    ceiling_raw: object = getattr(
        config.general, "auto_integrate_resolve_timeout_seconds", None
    )
    options = InvokeOptions(
        workspace_path=root,
        requires_completion_evidence=False,
        idle_timeout_seconds=_positive_float(idle_raw, IDLE_TIMEOUT_SECONDS),
        max_session_seconds=_positive_float(
            ceiling_raw, _DEFAULT_RESOLVE_TIMEOUT_SECONDS
        ),
    )
    for _line in invoke_agent(agent_config, str(prompt_path), options=options):
        pass


def _positive_float(value: object, fallback: float) -> float:
    """Coerce a config value to a positive float, else ``fallback``.

    Guards against a partially-constructed config (as built by mocks in
    tests) silently disabling a watchdog.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return fallback


def _emit_warn(display: ParallelDisplay, message: str) -> None:
    """Emit an operator-facing WARN line; never raises."""
    with contextlib.suppress(Exception):
        display.emit_warn_line("run", "auto-integrate", message)
