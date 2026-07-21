"""Dev-agent conflict resolver builder for the auto-integrate step.

Builds the production :data:`~ralph.pipeline.auto_integrate_resolve.ConflictResolver`
handed to :func:`ralph.pipeline.auto_integrate.auto_integrate_after_commit`:
a focused invocation of the FIRST agent bound to the ``development``
drain whose only job is to rewrite the conflicted files of the
in-progress endpoint merge in place. The agent never runs a git
command: Ralph stages the previously-conflicted paths and creates the
merge commit deterministically after verifying every conflict marker
is gone. Requiring the agent to stage would be unimplementable — an
agent running under Ralph's own MCP exec policy is denied every git
invocation.

Fault-tolerance contract: every failure mode (no dev agent configured,
prompt write failure, agent invocation error) returns ``False`` so the
integration step aborts the merge and records a conflict instead of
crashing the run. The builder itself never raises.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.invoke import InvokeOptions, invoke_agent
from ralph.git.merge import unmerged_paths

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from typing import Protocol

    from ralph.config.models import AgentConfig
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
) -> ConflictResolver:
    """Build the dev-agent conflict resolver for the integration step.

    The returned callable matches
    :data:`~ralph.pipeline.auto_integrate_resolve.ConflictResolver`:
    it is invoked with ``(repo_root, target_branch)`` while the
    conflicted merge is in progress and returns True only when the
    agent invocation completed, meaning the conflicts should now be
    resolved and staged (the caller still verifies deterministically).
    """

    def _resolver(root: Path, target: str) -> bool:
        agent_name = _resolver_agent_name(policy_bundle)
        if agent_name is None:
            logger.warning(
                "auto_integrate: no agent bound to drain '{}'; cannot resolve",
                _RESOLVER_DRAIN,
            )
            return False
        agent_config = registry.get(agent_name)
        if agent_config is None:
            logger.warning(
                "auto_integrate: agent '{}' not in registry; cannot resolve",
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
            _drain_invocation(agent_config, prompt_path, root)
        except Exception as invoke_exc:
            logger.warning(
                "auto_integrate: conflict-resolution invocation failed: {}",
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

    return _resolver


def _resolver_agent_name(policy_bundle: PolicyBundle) -> str | None:
    """First agent of the chain bound to the ``development`` drain."""
    drain_binding = policy_bundle.agents.agent_drains.get(_RESOLVER_DRAIN)
    if drain_binding is None:
        return None
    chain_config = policy_bundle.agents.agent_chains.get(drain_binding.chain)
    if chain_config is None or not chain_config.agents:
        return None
    return chain_config.agents[0]


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
    agent_config: AgentConfig, prompt_path: Path, root: Path
) -> None:
    """Run the agent invocation to completion, discarding output lines."""
    options = InvokeOptions(
        workspace_path=root,
        requires_completion_evidence=False,
    )
    for _line in invoke_agent(agent_config, str(prompt_path), options=options):
        pass


def _emit_warn(display: ParallelDisplay, message: str) -> None:
    """Emit an operator-facing WARN line; never raises."""
    with contextlib.suppress(Exception):
        display.emit_warn_line("run", "auto-integrate", message)
