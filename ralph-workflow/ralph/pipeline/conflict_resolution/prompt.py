"""Renders the conflict-only prompt for one resolution round.

The resolution session must see the CONFLICT and nothing else. A prompt
carrying the run's source-prompt or plan payload would invite the agent
to resume feature work inside an in-progress merge, which is precisely
the state in which unrelated edits are most expensive to unpick. The
template is therefore rendered with a closed variable set and no project
payload section at all.

"Closed" means closed to the RUN's payload, not to the session's own
contract. The standard preamble every Ralph session gets -- unattended
mode, the granted capabilities, and the brokered MCP tool surface -- is
rendered here too, because a resolution session that is not told it must
edit through ``write_file`` will reach for a native tool that the MCP
broker has disabled. Only the ARTIFACT SUBMISSION block is suppressed:
this session submits nothing, it edits files and calls
``declare_complete``.

The rendered file lands at ``.agent/tmp/<phase>_prompt.md`` -- the same
location :func:`ralph.prompts.debug_dump.prompt_dump_path` already uses
for every in-graph phase, so it is covered by the existing
runtime-artifact rules rather than introducing a new path shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.mcp.tools.names import claude_tool_name_prefix
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.prompts.debug_dump import prompt_dump_path
from ralph.prompts.template_engine import render_template
from ralph.prompts.template_registry import (
    load_partial_templates,
    packaged_template_root,
)
from ralph.prompts.template_variables import (
    capability_template_variables,
    default_caps_and_flags_for_drain,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ralph.mcp.artifacts.file_backend import FileBackend

#: Packaged Jinja template holding the resolution prompt body. Kept
#: alongside the pipeline prompt templates so it follows the same
#: convention as every other prompt.
PROMPT_TEMPLATE_NAME = "conflict_resolution.jinja"

#: Drain class the resolution drain is bound to in
#: ``ralph/policy/defaults/agents.toml``. Its defaults are what the
#: session is really granted, so rendering the capability preamble from
#: them keeps the prompt and the session in lock-step instead of
#: describing a capability set the agent does not have.
_RESOLUTION_DRAIN = SessionDrain.DEVELOPMENT

#: Body used when the conflicted-path query returned nothing, so the agent
#: still has an actionable instruction rather than an empty list.
_UNKNOWN_PATHS_BODY = (
    "The conflicted paths could not be listed. Search the repository\n"
    "for files containing `<<<<<<< ` conflict markers and resolve every\n"
    "one of them."
)

__all__ = ["PROMPT_TEMPLATE_NAME", "render_conflict_prompt"]


def render_conflict_prompt(
    *,
    root: Path,
    target: str,
    conflicted_paths: Sequence[str],
    round_index: int,
    round_cap: int,
    surviving_marker_paths: Sequence[str],
    replaying_commit_sha: str | None = None,
    replaying_commit_subject: str | None = None,
    stop_index: int | None = None,
    stop_cap: int | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> Path | None:
    """Render and materialize the prompt for one resolution round.

    Args:
        root: Repository root holding the in-progress merge.
        target: Mainline branch being merged in.
        conflicted_paths: Paths git reported as unmerged.
        round_index: 1-based index of the round about to run.
        round_cap: Total rounds allowed.
        surviving_marker_paths: Paths that still carried conflict markers
            after the PREVIOUS round. Empty on round 1. This is what makes
            the loop converge instead of repeat.
        replaying_commit_sha: SHA of the commit the rebase stopped on.
            Setting it puts the prompt in REBASE mode; leaving it ``None``
            renders the endpoint-merge prompt byte-identically to before.
        replaying_commit_subject: Subject line of that commit.
        stop_index: 1-based index of the rebase stop being resolved.
        stop_cap: Total rebase stops allowed.
        backend: File backend seam, injected for tests.

    The rebase-mode variables add the ONE fact a merge conflict does not
    have -- which commit is being replayed -- and nothing else. The
    context stays the conflict alone: no source prompt, no plan, no
    artifact history, no analysis feedback.

    Returns:
        The path the prompt was written to, or ``None`` when it could not
        be written. Never raises: a prompt that cannot be materialized
        fails the round, it does not fail the run.
    """
    variables = {
        "repo_root": str(root),
        "target": target,
        "conflicted_block": _conflicted_block(conflicted_paths),
        "round_index": str(round_index),
        "round_cap": str(round_cap),
        "feedback_block": _feedback_block(surviving_marker_paths),
        "replaying_commit_sha": replaying_commit_sha or "",
        "replaying_commit_subject": replaying_commit_subject or "",
        "stop_index": str(stop_index) if stop_index is not None else "",
        "stop_cap": str(stop_cap) if stop_cap is not None else "",
        **_session_preamble_variables(),
    }
    template_root = packaged_template_root()
    try:
        template_text = (template_root / PROMPT_TEMPLATE_NAME).read_text(
            encoding="utf-8"
        )
        partials = load_partial_templates((template_root,))
        rendered = render_template(template_text, variables, partials)
    except Exception as render_exc:
        logger.warning(
            "conflict_resolution: failed to render the resolution prompt: {}",
            render_exc,
        )
        return None

    prompt_path = root / prompt_dump_path(PHASE_RESOLUTION)
    try:
        backend.mkdir(prompt_path.parent, parents=True, exist_ok=True)
        write_text_if_changed(backend, prompt_path, rendered, encoding="utf-8")
    except OSError as write_exc:
        logger.warning(
            "conflict_resolution: failed to write the resolution prompt: {}",
            write_exc,
        )
        return None
    return prompt_path


def _session_preamble_variables() -> dict[str, str]:
    """Capability and MCP-tool variables the shared preamble partials need.

    ``HIDE_ARTIFACT_SUBMISSION_GUIDANCE`` is the one deliberate
    subtraction: the resolution session's completion contract is
    ``declare_complete`` alone, and offering it the artifact-submission
    surface would invite a ``development_result`` for work that is not a
    development iteration.

    ``SKILLS_INLINE_CONTENT`` is required by ``shared/_shipped_skills.j2``
    and the renderer uses ``StrictUndefined``, so it must be present.
    Empty selects the generic "use whatever skills your environment
    exposes" wording, which is the right guidance here: this session is
    handed a conflict, not an execution plan with a skills section.
    """
    capabilities, policy_flags = default_caps_and_flags_for_drain(_RESOLUTION_DRAIN)
    variables = capability_template_variables(
        capabilities,
        policy_flags,
        tool_name_prefix=claude_tool_name_prefix(),
    )
    variables["HIDE_ARTIFACT_SUBMISSION_GUIDANCE"] = "true"
    variables["SKILLS_INLINE_CONTENT"] = ""
    return variables


def _conflicted_block(conflicted_paths: Sequence[str]) -> str:
    """Render the conflicted-path bullet list, or the search fallback."""
    listed = [path for path in conflicted_paths if path.strip()]
    if not listed:
        return _UNKNOWN_PATHS_BODY
    return "\n".join(f"- `{path}`" for path in listed)


def _feedback_block(surviving_marker_paths: Sequence[str]) -> str:
    """Render the previous round's surviving-marker feedback, or ``''``.

    Empty on the first round. On later rounds it names the files that
    still contained conflict markers, which is the reason this round
    exists at all.
    """
    listed = [path for path in surviving_marker_paths if path.strip()]
    if not listed:
        return ""
    bullets = "\n".join(f"- `{path}`" for path in listed)
    return (
        "\n## Why this round exists\n\n"
        "After the previous round these files STILL contained conflict "
        "markers:\n\n"
        f"{bullets}\n\n"
        "Resolve them completely this time. Every `<<<<<<<`, `=======` and "
        "`>>>>>>>` line must be gone.\n"
    )
