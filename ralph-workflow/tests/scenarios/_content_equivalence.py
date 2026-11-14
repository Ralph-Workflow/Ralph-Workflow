from __future__ import annotations

import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
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
    capture_operator_lines,
    commit_file,
    git,
)
from tests.scenarios._counting_resolver import CountingResolver
from tests.scenarios._fast_import_commit import FastImportCommit
from tests.scenarios._integration_run import IntegrationRun

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver

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


def lifecycle_config(**conflict_resolution_overrides: object) -> UnifiedConfig:
    payload: dict[str, object] = {
        "general": {
            "auto_integrate_enabled": True,
            "auto_integrate_target": "main",
            "auto_integrate_remote_enabled": False,
        },
        "conflict_resolution": {
            "max_rounds_per_stop": 2,
            "max_rebase_conflict_stops": 4,
        },
    }
    limits = payload["conflict_resolution"]
    assert isinstance(limits, dict)
    limits.update(conflict_resolution_overrides)
    return UnifiedConfig.model_validate(payload)


def fast_import_commits(root: Path, commits: Sequence[FastImportCommit]) -> None:
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
    git(root, "checkout", "-q", "-b", branch)
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(root, "add", "--", *files)
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


@dataclass(frozen=True)
class ContentEquivalence:
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


def _tree_entries(root: Path, ref: str) -> dict[str, str]:
    output = git(root, "ls-tree", "-r", ref, check=False)
    entries: dict[str, str] = {}
    for line in output.splitlines():
        metadata, separator, path = line.partition("\t")
        fields = metadata.split()
        if separator and path and len(fields) >= 3:
            entries[path] = fields[2]
    return entries


def _merge_base(root: Path, target: str, feature: str) -> str:
    return git(root, "merge-base", target, feature, check=False)


def _changed_paths(root: Path, base: str, feature: str) -> tuple[str, ...]:
    output = git(root, "diff", "--name-only", "-z", base, feature, check=False)
    return tuple(entry for entry in output.split("\0") if entry)


def snapshot_uncommitted(root: Path, paths: Iterable[str]) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for relative in paths:
        candidate = root / relative
        if candidate.is_file():
            snapshot[relative] = candidate.read_bytes()
    return snapshot


def _find_missing_paths(
    target: str,
    feature: str,
    resolved_base: str,
    feature_paths: Sequence[str],
    excluded: set[str],
    trees: Mapping[str, Mapping[str, str]],
) -> list[str]:
    missing: list[str] = []
    for relative in feature_paths:
        if relative in excluded:
            continue
        feature_blob = trees[feature].get(relative, f"{feature}:{relative}")
        base_blob = (
            trees[resolved_base].get(relative, f"{resolved_base}:{relative}")
            if resolved_base
            else ""
        )
        target_blob = trees[target].get(relative, f"{target}:{relative}")
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
    return missing


def content_equivalence(
    root: Path,
    *,
    target: str = "main",
    feature: str = "HEAD",
    base: str | None = None,
    excluded_paths: Sequence[str] = (),
    source_refs: Sequence[str] = (),
    preserved: Mapping[str, bytes] | None = None,
) -> ContentEquivalence:
    excluded = set(excluded_paths)
    resolved_base = base if base is not None else _merge_base(root, target, feature)
    unexpected: list[str] = []
    lost: list[str] = []
    refs = {target, feature, *source_refs}
    if resolved_base:
        refs.add(resolved_base)
    trees = {ref: _tree_entries(root, ref) for ref in refs}

    if resolved_base:
        feature_paths = _changed_paths(root, resolved_base, feature)
        base_paths = set(trees[resolved_base])
    else:
        feature_paths = tuple(trees[feature])
        base_paths = set()

    missing = _find_missing_paths(
        target,
        feature,
        resolved_base,
        feature_paths,
        excluded,
        trees,
    )

    allowed: set[str] = set()
    for ref in (*source_refs, feature):
        allowed.update(trees[ref])
    if resolved_base:
        allowed.update(base_paths)
    for relative in trees[target]:
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


def drive_integration(
    root: Path,
    *,
    config: UnifiedConfig | None = None,
    resolver: RebaseStopResolver | None = None,
    conflict_resolver: ConflictResolver | None = None,
    state: RebaseState | None = None,
) -> IntegrationRun:
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
