from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from loguru import logger

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.mcp.tools.names import claude_tool_name_prefix
from ralph.phases.required_artifacts import read_validation_retry_hint
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
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

PROMPT_TEMPLATE_NAME = "conflict_resolution.jinja"

_RESOLUTION_DRAIN = SessionDrain.DEVELOPMENT

_UNKNOWN_PATHS_BODY = (
    "The conflicted paths could not be listed. Search the repository\n"
    "for files containing `<<<<<<< ` conflict markers and resolve every\n"
    "one of them."
)

__all__ = [
    "PROMPT_TEMPLATE_NAME",
    "PromptMaterializer",
    "materialize_round_prompt",
    "render_conflict_prompt",
]


class PromptMaterializer(Protocol):
    def __call__(
        self,
        *,
        root: Path,
        target: str,
        conflicted_paths: Sequence[str],
        round_index: int,
        round_cap: int,
        session: ResolutionSession,
        stop: RebaseStop | None,
        strategy_history: tuple[str, ...],
    ) -> Path | None: ...


def materialize_round_prompt(
    *,
    root: Path,
    target: str,
    conflicted_paths: Sequence[str],
    round_index: int,
    round_cap: int,
    session: ResolutionSession,
    stop: RebaseStop | None,
    strategy_history: tuple[str, ...] = (),
) -> Path | None:
    from ralph.pipeline.conflict_resolution._driver_events import render_round_prompt

    return render_round_prompt(
        root, target, conflicted_paths, round_index, round_cap, session, stop, strategy_history
    )


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
    strategy_history: Sequence[str] = (),
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> Path | None:
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
        "strategy_history": "\n".join(strategy_history),
        "LAST_RETRY_ERROR": read_validation_retry_hint(root, _RESOLUTION_DRAIN),
        **_session_preamble_variables(),
    }
    template_root = packaged_template_root()
    try:
        template_text = (template_root / PROMPT_TEMPLATE_NAME).read_text(encoding="utf-8")
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
    listed = [path for path in conflicted_paths if path.strip()]
    if not listed:
        return _UNKNOWN_PATHS_BODY
    return "\n".join(f"- `{path}`" for path in listed)


def _feedback_block(surviving_marker_paths: Sequence[str]) -> str:
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
