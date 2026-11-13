from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.project_policy import analysis, markers, preflight

if TYPE_CHECKING:
    from ralph.workspace.protocol import Workspace

_INSTRUCTION_FILES: tuple[str, ...] = (markers.AGENTS_MD, markers.CLAUDE_MD)

_SCRATCH_PATHS: tuple[str, ...] = (
    markers.CACHE_REL_PATH,
    preflight.REMEDIATION_PROMPT_REL_PATH,
    analysis.ANALYSIS_PROMPT_REL_PATH,
    analysis.ANALYSIS_ARTIFACT_REL_PATH,
)


def _strip_managed_block(content: str) -> str:
    begin_match = markers.AGENTS_BLOCK_BEGIN_RE.search(content)
    end = content.find(markers.AGENTS_BLOCK_END)
    if begin_match is None or end == -1 or end < begin_match.start():
        return content
    begin = begin_match.start()
    tail = end + len(markers.AGENTS_BLOCK_END)
    stripped = content[:begin] + content[tail:]
    return stripped.replace("\n\n\n", "\n\n")


def _strip_opt_out(content: str) -> str:
    return content.replace(markers.OPT_OUT_MARKER + "\n", "").replace(markers.OPT_OUT_MARKER, "")


def _strip_migrated_markers(content: str) -> str:
    lines = [
        line
        for line in content.splitlines(keepends=True)
        if markers.MIGRATED_MARKER_PREFIX not in line
    ]
    return "".join(lines)


def _rewrite(workspace: Workspace, path: str, changed: list[str]) -> None:
    if not workspace.exists(path):
        return
    content = workspace.read(path)
    updated = _strip_migrated_markers(_strip_opt_out(_strip_managed_block(content)))
    if updated != content:
        workspace.write(path, updated)
        changed.append(path)


def reset_policy_state(workspace: Workspace) -> list[str]:
    changed: list[str] = []

    canonical = markers.CANONICAL_DIR.rstrip("/")
    for path in workspace.iter_files(canonical):
        workspace.delete(path)
        changed.append(path)
    if workspace.is_dir(canonical):
        workspace.delete(canonical, recursive=True)

    for path in _SCRATCH_PATHS:
        if workspace.exists(path):
            workspace.delete(path)
            changed.append(path)

    for path in (*_INSTRUCTION_FILES, *markers.MIGRATION_CANDIDATE_PATHS):
        _rewrite(workspace, path, changed)

    return changed


__all__ = ["reset_policy_state"]
