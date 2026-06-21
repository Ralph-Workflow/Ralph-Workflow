"""Canonical allowlist of Ralph Workflow runtime-artifact paths.

This module is the single source of truth for which paths the
``commit_cleanup`` phase treats as engine-owned and unconditionally
deletable. Three places in Ralph must agree on this allowlist:

1. ``ralph/phases/commit_cleanup.py`` -- the fast-path exemption that
   bypasses the universal HEAD veto in ``_is_safe_to_delete``.
2. ``ralph/config/bootstrap.py`` -- the patterns seeded into
   ``.gitignore`` (root-anchored) and ``.git/info/exclude`` so these
   paths never enter the repo in the first place.
3. ``ralph/testing/audit_agent_internal_paths.py`` -- the regression
   audit that pins the canonical allowlist to its three consumers.

The inventory is derived from the following canonical sources (see
PLAN.md for the full reconciliation):

* ``_GENERATED_AGENT_STATE_FILES`` in ``ralph.cli.commands.run``
  (12 basenames)
* ``_GENERATED_AGENT_STATE_DIRS`` in ``ralph.cli.commands.run`` (4 dirs)
* ``HANDOFF_PATHS`` in ``ralph.mcp.artifacts.handoffs`` (adds
  ``PLANNING_ANALYSIS_DECISION.md`` -- the PA-001 gap)
* ``COMPLETION_SENTINEL_RELPATHFMT`` in ``ralph.mcp.tools.coordination``
  (``completion_seen_*.json`` filename glob)
* ``RECEIPT_DIR_RELPATH_FMT`` in ``ralph.mcp.artifacts.completion_receipts``
  (``receipts/``)
* ``_BASELINE_FILENAME`` in ``ralph.pipeline.cycle_baseline``
  (``start_commit`` -- already in ``_GENERATED_AGENT_STATE_FILES``)
* Engine-written working-tree artifacts: ``artifact-formats``,
  log-growth probe ``raw``
* Engine-owned config files inside ``.agent/``: ``mcp.toml``
* Root-level (repo root, outside ``.agent/``): ``checkpoint.json``

The module uses stdlib-only imports so it cannot introduce import cycles
regardless of which package consumes it.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

# Top-level basenames under ``.agent/``. Derived from
# ``_GENERATED_AGENT_STATE_FILES`` (12 entries) + ``HANDOFF_PATHS``
# (``PLANNING_ANALYSIS_DECISION.md`` is the PA-001 gap) + bootstrap's
# ``_LOCAL_MCP_FILENAME`` (``mcp.toml``). Case-sensitive -- matches the
# canonical renderer usage.
AGENT_INTERNAL_TOP_LEVEL_BASENAMES: frozenset[str] = frozenset(
    {
        "CURRENT_PROMPT.md",
        "PLAN.md",
        "ISSUES.md",
        "DEVELOPMENT_RESULT.md",
        "FIX_RESULT.md",
        "DEVELOPMENT_ANALYSIS_DECISION.md",
        "PLANNING_ANALYSIS_DECISION.md",
        "REVIEW_ANALYSIS_DECISION.md",
        "checkpoint.json",
        "rebase_checkpoint.json",
        "rebase_checkpoint.json.bak",
        "rebase.lock",
        "start_commit",
        "mcp.toml",
    }
)

# Directory segments under ``.agent/`` whose contents are engine-owned.
# Derived from ``_GENERATED_AGENT_STATE_DIRS`` (4 entries:
# ``artifacts``, ``tmp``, ``prompt_history``, ``workers``) +
# ``RECEIPT_DIR_RELPATH_FMT`` (``receipts``) + log-growth probe
# (``raw``) + working-tree artifact mirror (``artifact-formats``).
AGENT_INTERNAL_DIR_GLOBS: frozenset[str] = frozenset(
    {
        "artifacts",
        "tmp",
        "prompt_history",
        "workers",
        "receipts",
        "raw",
        "artifact-formats",
    }
)

# Basenames that are engine-owned ONLY when they appear at the repo root.
# ``checkpoint.json`` is the canonical root-level variant (per
# ``ralph.pipeline.checkpoint``); the ``.agent/`` variant is covered by
# ``AGENT_INTERNAL_TOP_LEVEL_BASENAMES`` instead.
AGENT_INTERNAL_ROOT_BASENAMES: frozenset[str] = frozenset(
    {
        "checkpoint.json",
    }
)

# Canonical on-disk filename glob for completion sentinels. Confirmed
# against ``COMPLETION_SENTINEL_RELPATHFMT`` in
# ``ralph.mcp.tools.coordination``: ``.agent/completion_seen_{run_id}.json``.
# This is the on-disk filename pattern -- NOT the Python abstraction
# identifier ``completion_sentinel_*`` which never appears on disk.
_AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB: str = "completion_seen_*.json"

# Path-segment count constants for the ``is_agent_internal_path`` predicate.
# These are intentionally named so the predicate's branches read as
# ``len(parts) == _SEGMENT_COUNT_TWO`` rather than ``len(parts) == 2``.
_SEGMENT_COUNT_ONE: int = 1
_SEGMENT_COUNT_TWO: int = 2


def is_agent_internal_path(path: str) -> bool:
    """Return True when ``path`` is a Ralph Workflow runtime artifact.

    The check is segment-aware -- it does NOT do a path-prefix match on
    ``.agent/``, so user-authored tracked files under ``.agent/`` (e.g.
    ``.agent/test.py``) are correctly rejected.

    A path is agent-internal when:

    1. It is a single-segment path and the basename is in
       ``AGENT_INTERNAL_ROOT_BASENAMES`` (e.g. ``checkpoint.json``), OR
    2. The first segment is ``.agent`` AND the second segment is in
       ``AGENT_INTERNAL_DIR_GLOBS`` (e.g. ``.agent/raw/x.log``), OR
    3. The first segment is ``.agent`` AND the second segment is in
       ``AGENT_INTERNAL_TOP_LEVEL_BASENAMES`` (e.g. ``.agent/PLAN.md``),
       OR
    4. The first segment is ``.agent`` AND the basename matches
       ``_AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB``
       (e.g. ``.agent/completion_seen_run-1.json``).

    Args:
        path: Repository-relative POSIX path (forward slashes).

    Returns:
        True when the path is an engine-owned runtime artifact.
    """
    if not path:
        return False

    candidate = Path(path)
    parts = candidate.parts
    name = candidate.name

    # Case 1: root-level single-segment path.
    if len(parts) == _SEGMENT_COUNT_ONE:
        return name in AGENT_INTERNAL_ROOT_BASENAMES

    # Anything below ``.agent/`` falls into cases 2/3/4 below. Anything
    # outside ``.agent/`` is never agent-internal -- reject early so a
    # path like ``app/checkpoint.json`` does not get confused with the
    # root-level variant.
    if parts[0] != ".agent":
        return False

    # Beyond ``.agent/``, the second segment (case 2), the second segment
    # being a top-level basename (case 3), or the basename matching the
    # completion-sentinel glob (case 4) all qualify.
    if len(parts) == _SEGMENT_COUNT_TWO:
        second = parts[1]
        return (
            second in AGENT_INTERNAL_DIR_GLOBS
            or second in AGENT_INTERNAL_TOP_LEVEL_BASENAMES
            or fnmatch.fnmatchcase(name, _AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB)
        )

    # Three or more segments under ``.agent/``: the second segment must
    # be in the directory globs. We deliberately do NOT inspect deeper
    # segments so user-authored source files inside ``.agent/raw/`` are
    # not silently accepted -- the ``raw`` directory is a Ralph-internal
    # scratch area but the agent never writes source code there, so this
    # widens only what Ralph itself writes.
    return parts[1] in AGENT_INTERNAL_DIR_GLOBS
