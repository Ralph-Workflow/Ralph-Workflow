"""Shared support for the integration-lifecycle scenario catalog.

The module carries two things the scenario files share:

* ``assert_content_equivalence`` -- the reusable group-10 assertion helper.
  Every landing scenario calls it so that "the work reached the target" is
  always checked as *content*, never as a recorded SHA.
* small builders that drive the real integration entry point with the real
  ``tmp_git_repo`` template, so no scenario needs its own virtualenv, clock
  sleep or per-commit shell loop.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_harness import (
    AgentMode,
    FakeAgentRunner,
    OperatorLine,
    capture_operator_lines,
    commit_file,
    git,
)

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStop, RebaseStopResolver

pytestmark = pytest.mark.subprocess_e2e

__all__ = [
    "ContentEquivalence",
    "CountingResolver",
    "FastImportCommit",
    "IntegrationRun",
    "assert_content_equivalence",
    "build_feature_branch",
    "commit_file",
    "content_equivalence",
    "drive_integration",
    "fast_import_commits",
    "git",
    "lifecycle_config",
    "snapshot_uncommitted",
]


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def lifecycle_config(**conflict_resolution_overrides: object) -> UnifiedConfig:
    """The scenario configuration, with optional conflict-resolution overrides.

    The defaults are exactly the harness' ``scenario_config()`` values, so a
    scenario that does not override anything runs with the same budget as every
    other lifecycle scenario.
    """
    payload: dict[str, object] = {
        "general": {
            "auto_integrate_enabled": True,
            "auto_integrate_target": "main",
            "auto_integrate_remote_enabled": False,
        },
        "conflict_resolution": {
            "max_rounds_per_stop": 2,
            "max_rebase_conflict_stops": 4,
            "total_resolution_cap_seconds": 10,
        },
    }
    limits = payload["conflict_resolution"]
    assert isinstance(limits, dict)
    limits.update(conflict_resolution_overrides)
    return UnifiedConfig.model_validate(payload)


# --------------------------------------------------------------------------
# git plumbing helpers
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FastImportCommit:
    """One commit for :func:`fast_import_commits`."""

    ref: str
    message: str
    files: tuple[tuple[str, bytes], ...]
    parent_index: int | None = None
    parent_sha: str | None = None


def fast_import_commits(root: Path, commits: Sequence[FastImportCommit]) -> None:
    """Create many commits with a single ``git fast-import`` invocation.

    ``git fast-import`` is plain git plumbing: one process, no per-commit shell
    loop, which keeps the large-work scenario inside the suite time budget.
    """
    stream = bytearray()
    blob_marks: list[list[int]] = []
    commit_marks: list[int] = []
    mark = 0
    for commit in commits:
        mark += 1
        commit_marks.append(mark)
        commit_blobs: list[int] = []
        for _path, payload in commit.files:
            mark += 1
            commit_blobs.append(mark)
            stream += b"blob\n"
            stream += f"mark :{mark}\n".encode()
            stream += f"data {len(payload)}\n".encode()
            stream += payload
            stream += b"\n"
        blob_marks.append(commit_blobs)
    for index, commit in enumerate(commits):
        message = commit.message.encode()
        stream += f"commit {commit.ref}\n".encode()
        stream += f"mark :{commit_marks[index]}\n".encode()
        stream += b"committer Scenario <scenario@example.com> 1700000000 +0000\n"
        stream += f"data {len(message)}\n".encode()
        stream += message
        stream += b"\n"
        if commit.parent_index is not None:
            stream += f"from :{commit_marks[commit.parent_index]}\n".encode()
        elif commit.parent_sha is not None:
            stream += f"from {commit.parent_sha}\n".encode()
        for (path, _payload), blob_mark in zip(commit.files, blob_marks[index], strict=True):
            stream += f"M 100644 :{blob_mark} {path}\n".encode()
        stream += b"\n"
    stream += b"done\n"
    completed = subprocess.run(
        ("git", "fast-import", "--quiet", "--done"),
        cwd=root,
        input=bytes(stream),
        capture_output=True,
        check=False,
        timeout=30,
        env={"HOME": str(root / ".scenario-home"), "PATH": "/usr/bin:/bin"},
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git fast-import failed ({completed.returncode}): "
            f"{completed.stderr.decode(errors='replace').strip()}"
        )


def build_feature_branch(
    root: Path,
    *,
    files: Mapping[str, str],
    message: str = "feature change",
    branch: str = "feature",
) -> str:
    """Create ``branch`` from the current target tip with one commit."""
    git(root, "checkout", "-q", "-b", branch)
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(root, "add", "--", *files)
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


# --------------------------------------------------------------------------
# content equivalence (catalog group 10)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ContentEquivalence:
    """The three content-equivalence obligations of catalog group 10."""

    missing: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()
    lost: tuple[str, ...] = ()

    @property
    def satisfied(self) -> bool:
        return not (self.missing or self.unexpected or self.lost)

    def describe(self) -> str:
        parts: list[str] = []
        if self.missing:
            parts.append("feature change absent from the target: " + ", ".join(self.missing))
        if self.unexpected:
            parts.append(
                "unrelated content introduced on the target: " + ", ".join(self.unexpected)
            )
        if self.lost:
            parts.append("uncommitted work lost: " + ", ".join(self.lost))
        return "; ".join(parts) if parts else "content equivalence holds"


def _tree_paths(root: Path, ref: str) -> tuple[str, ...]:
    output = git(root, "ls-tree", "-r", "--name-only", ref, check=False)
    return tuple(line for line in output.splitlines() if line)


def _blob_oid(root: Path, ref: str, path: str) -> str:
    return git(root, "rev-parse", f"{ref}:{path}", check=False)


def _merge_base(root: Path, target: str, feature: str) -> str:
    return git(root, "merge-base", target, feature, check=False)


def _changed_paths(root: Path, base: str, feature: str) -> tuple[str, ...]:
    output = git(root, "diff", "--name-only", "-z", base, feature, check=False)
    return tuple(entry for entry in output.split("\0") if entry)


def snapshot_uncommitted(root: Path, paths: Iterable[str]) -> dict[str, bytes]:
    """Snapshot the bytes of worktree paths that are not committed."""
    snapshot: dict[str, bytes] = {}
    for relative in paths:
        candidate = root / relative
        if candidate.is_file():
            snapshot[relative] = candidate.read_bytes()
    return snapshot


def content_equivalence(  # noqa: PLR0912
    root: Path,
    *,
    target: str = "main",
    feature: str = "HEAD",
    base: str | None = None,
    excluded_paths: Sequence[str] = (),
    source_refs: Sequence[str] = (),
    preserved: Mapping[str, bytes] | None = None,
) -> ContentEquivalence:
    """Compare the feature's content with the target's content.

    * every non-excluded change the feature made must be visible on the
      target (the target must not still carry the pre-feature content),
    * nothing may appear on the target that comes from neither the feature,
      the merge base nor the target's own pre-integration state,
    * uncommitted worktree content must still be present afterwards.
    """
    excluded = set(excluded_paths)
    resolved_base = base if base is not None else _merge_base(root, target, feature)
    missing: list[str] = []
    unexpected: list[str] = []
    lost: list[str] = []

    if resolved_base:
        feature_paths = _changed_paths(root, resolved_base, feature)
        base_paths = set(_tree_paths(root, resolved_base))
    else:
        feature_paths = _tree_paths(root, feature)
        base_paths = set()

    for relative in feature_paths:
        if relative in excluded:
            continue
        feature_blob = _blob_oid(root, feature, relative)
        base_blob = _blob_oid(root, resolved_base, relative) if resolved_base else ""
        target_blob = _blob_oid(root, target, relative)
        if feature_blob == base_blob:
            continue
        if not feature_blob:
            if target_blob:
                missing.append(f"{relative} (the feature deleted it; the target kept it)")
            continue
        if not target_blob:
            missing.append(f"{relative} (absent on the target)")
            continue
        if target_blob == base_blob:
            missing.append(f"{relative} (the target still carries the pre-feature content)")

    allowed: set[str] = set()
    for ref in (*source_refs, feature):
        allowed.update(_tree_paths(root, ref))
    if resolved_base:
        allowed.update(base_paths)
    for relative in _tree_paths(root, target):
        if relative not in allowed:
            unexpected.append(relative)

    for relative, payload in (preserved or {}).items():
        candidate = root / relative
        if not candidate.is_file() or candidate.read_bytes() != payload:
            lost.append(relative)

    return ContentEquivalence(
        missing=tuple(missing),
        unexpected=tuple(unexpected),
        lost=tuple(lost),
    )


def assert_content_equivalence(
    root: Path,
    *,
    contract: str,
    target: str = "main",
    feature: str = "HEAD",
    base: str | None = None,
    excluded_paths: Sequence[str] = (),
    source_refs: Sequence[str] = (),
    preserved: Mapping[str, bytes] | None = None,
) -> ContentEquivalence:
    """Assert catalog group 10 and return the report for further assertions."""
    report = content_equivalence(
        root,
        target=target,
        feature=feature,
        base=base,
        excluded_paths=excluded_paths,
        source_refs=source_refs,
        preserved=preserved,
    )
    assert report.satisfied, f"{contract} -- {report.describe()}"
    return report


# --------------------------------------------------------------------------
# driving the real integration entry point
# --------------------------------------------------------------------------


class CountingResolver:
    """Wrap a rebase-stop resolver and count how often it is launched."""

    def __init__(self, inner: RebaseStopResolver) -> None:
        self.inner = inner
        self.launches = 0
        self.stops: list[RebaseStop] = []

    def __call__(self, root: Path, target: str, stop: RebaseStop) -> bool:
        self.launches += 1
        self.stops.append(stop)
        return bool(self.inner(root, target, stop))


@dataclass(frozen=True)
class IntegrationRun:
    """What one integration tick did."""

    state: RebaseState | None
    error: str | None
    operator_lines: tuple[OperatorLine, ...] = ()
    resolver_launches: int = 0
    facts: dict[str, str] = field(default_factory=dict)

    @property
    def failed_with_exception(self) -> bool:
        return self.error is not None


def drive_integration(
    root: Path,
    *,
    config: UnifiedConfig | None = None,
    resolver: RebaseStopResolver | None = None,
    conflict_resolver: ConflictResolver | None = None,
    state: RebaseState | None = None,
) -> IntegrationRun:
    """Run one integration tick against ``root`` and report what happened."""
    effective_config = config if config is not None else lifecycle_config()
    inner = resolver if resolver is not None else FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    counting = CountingResolver(inner)
    with capture_operator_lines() as lines:
        try:
            result = integrate_before_planning(
                effective_config,
                WorkspaceScope(root),
                state if state is not None else RebaseState(),
                conflict_resolver=conflict_resolver,
                rebase_stop_resolver=counting,
            )
            error: str | None = None
        except Exception as exc:
            result = None
            error = f"{type(exc).__name__}: {exc}"
    return IntegrationRun(
        state=result,
        error=error,
        operator_lines=tuple(lines),
        resolver_launches=counting.launches,
    )
