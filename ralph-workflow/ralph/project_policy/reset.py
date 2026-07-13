"""Wipe the project's policy state so it can be regenerated from scratch.

Backs ``--redo-policy`` and ``--redo-policy-only``.

WHY A FULL WIPE, AND NOT JUST A CACHE INVALIDATION
--------------------------------------------------

Deleting ``.agent/tmp/policy_readiness_cache.json`` looks like it should force a
regeneration. It does not. :func:`ralph.project_policy.starters.seed_starter_into`
only seeds a starter when the file is **absent**, so every existing policy file
survives the cache drop -- and if those files are structurally complete, the
deterministic validator passes them straight back to READY. The "redo" would be a
silent no-op, which is worse than no flag at all: the user believes their policy
was regenerated when nothing happened.

So the canonical directory is deleted outright. Everything else here exists to
leave no dangling reference to it.

All I/O goes through the :class:`~ralph.workspace.protocol.Workspace` seam, so
the reset is exercised against ``MemoryWorkspace`` in tests with no real
filesystem.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.project_policy import analysis, markers, preflight

if TYPE_CHECKING:
    from ralph.workspace.protocol import Workspace

#: Files whose managed block and opt-out marker are stripped by a reset.
_INSTRUCTION_FILES: tuple[str, ...] = (markers.AGENTS_MD, markers.CLAUDE_MD)

#: Scratch files the policy pipeline materializes. Stale copies would otherwise
#: be handed to the next run's agents as if they were current.
_SCRATCH_PATHS: tuple[str, ...] = (
    markers.CACHE_REL_PATH,
    preflight.REMEDIATION_PROMPT_REL_PATH,
    analysis.ANALYSIS_PROMPT_REL_PATH,
    analysis.ANALYSIS_ARTIFACT_REL_PATH,
)


def _strip_managed_block(content: str) -> str:
    """Remove the managed block, preserving every byte outside it.

    A user's own AGENTS.md content is theirs. The reset removes only what Ralph
    Workflow wrote, between (and including) its own markers.
    """
    begin = content.find(markers.AGENTS_BLOCK_BEGIN)
    end = content.find(markers.AGENTS_BLOCK_END)
    if begin == -1 or end == -1 or end < begin:
        return content
    tail = end + len(markers.AGENTS_BLOCK_END)
    stripped = content[:begin] + content[tail:]
    return stripped.replace("\n\n\n", "\n\n")


def _strip_opt_out(content: str) -> str:
    """Remove the opt-out marker.

    An explicit ``--redo-policy`` overrides a persisted opt-out: the user is
    standing at the terminal asking for policy right now, which outranks a marker
    they (or a teammate) committed at some point in the past.
    """
    return content.replace(markers.OPT_OUT_MARKER + "\n", "").replace(
        markers.OPT_OUT_MARKER, ""
    )


def _strip_migrated_markers(content: str) -> str:
    """Remove ``ralph-workflow-policy:migrated -> ...`` markers.

    These point at canonical files that this reset has just deleted. Left behind,
    they would tell the next remediation agent that a legacy doc was already
    reconciled into a file that no longer exists.
    """
    lines = [
        line
        for line in content.splitlines(keepends=True)
        if markers.MIGRATED_MARKER_PREFIX not in line
    ]
    return "".join(lines)


def _rewrite(workspace: Workspace, path: str, changed: list[str]) -> None:
    """Strip every policy marker from ``path``, recording it if it changed."""
    if not workspace.exists(path):
        return
    content = workspace.read(path)
    updated = _strip_migrated_markers(_strip_opt_out(_strip_managed_block(content)))
    if updated != content:
        workspace.write(path, updated)
        changed.append(path)


def reset_policy_state(workspace: Workspace) -> list[str]:
    """Delete every trace of the project's generated policy. Returns changed paths.

    Six steps:

    #. Delete the canonical policy directory outright (see the module docstring
       for why a cache drop is not enough).
    #. Delete the readiness cache.
    #. Delete the materialized remediation and analysis prompts.
    #. Delete any stale analysis decision artifact.
    #. Strip the managed block and the opt-out marker from AGENTS.md / CLAUDE.md,
       preserving all unmanaged content.
    #. Strip ``migrated ->`` markers from every migration-candidate document.

    Idempotent: a second call on an already-reset workspace changes nothing and
    returns an empty list.
    """
    changed: list[str] = []

    # Delete each policy file explicitly rather than relying on a recursive
    # directory delete. The workspace backends do not agree about directories:
    # MemoryWorkspace.exists() reports False for one, and its recursive delete
    # discards the directory entry while leaving the files beneath it. Only
    # iter_files() behaves the same on both. Enumerating is also more precise --
    # it removes what is actually there, including any file an agent added that
    # is not in CORE_POLICY_FILES.
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
