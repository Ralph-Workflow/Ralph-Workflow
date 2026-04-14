"""Classification helpers for Git rebase outcomes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import cast


class RebaseKind(Enum):
    """Enum describing every supported rebase failure mode."""

    INVALID_REVISION = "invalid_revision"
    DIRTY_WORKING_TREE = "dirty_working_tree"
    CONCURRENT_OPERATION = "concurrent_operation"
    REPOSITORY_CORRUPT = "repository_corrupt"
    ENVIRONMENT_FAILURE = "environment_failure"
    HOOK_REJECTION = "hook_rejection"
    CONTENT_CONFLICT = "content_conflict"
    PATCH_APPLICATION_FAILED = "patch_application_failed"
    INTERACTIVE_STOP = "interactive_stop"
    EMPTY_COMMIT = "empty_commit"
    AUTOSTASH_FAILED = "autostash_failed"
    COMMIT_CREATION_FAILED = "commit_creation_failed"
    REFERENCE_UPDATE_FAILED = "reference_update_failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RebaseErrorKind:
    """Payload for a classified rebase failure."""

    kind: RebaseKind
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


def _freeze_metadata(metadata: Mapping[str, object]) -> Mapping[str, object]:
    # Copy to prevent modification of the original dictionary before freezing.
    metadata_dict: dict[str, object] = dict(metadata)
    return cast("Mapping[str, object]", MappingProxyType(metadata_dict))


def classify_rebase_error(stderr: str, stdout: str) -> RebaseErrorKind:
    """Translate git rebase stderr/stdout into a concrete rebase failure kind."""

    output = f"{stderr}\n{stdout}".strip()

    for classifier in _REBASE_CLASSIFIERS:
        result = classifier(output)
        if result is not None:
            return result

    return RebaseErrorKind(
        kind=RebaseKind.UNKNOWN,
        metadata={"details": _extract_error_line(output)},
    )


def _classify_invalid_revision(output: str) -> RebaseErrorKind | None:
    triggers = [
        "invalid revision",
        "unknown revision",
        "bad revision",
        "ambiguous revision",
        "not found",
        "does not exist",
        "no such ref",
    ]
    if not any(trigger in output for trigger in triggers):
        return None

    revision = _extract_revision(output) or "unknown"
    return RebaseErrorKind(kind=RebaseKind.INVALID_REVISION, metadata={"revision": revision})


def _classify_shallow_or_missing_history(output: str) -> RebaseErrorKind | None:
    triggers = ["shallow", "depth", "unreachable", "needed single revision", "does not have"]
    if not any(trigger in output for trigger in triggers):
        return None

    return RebaseErrorKind(
        kind=RebaseKind.REPOSITORY_CORRUPT,
        metadata={"details": _extract_error_line(output)},
    )


def _classify_worktree_conflict(output: str) -> RebaseErrorKind | None:
    triggers = ["worktree", "checked out", "another branch", "already checked out"]
    if not any(trigger in output for trigger in triggers):
        return None

    return RebaseErrorKind(
        kind=RebaseKind.CONCURRENT_OPERATION,
        metadata={"operation": "branch checked out in another worktree"},
    )


def _classify_submodule_conflict(output: str) -> RebaseErrorKind | None:
    if ".gitmodules" not in output and "submodule" not in output:
        return None

    return RebaseErrorKind(
        kind=RebaseKind.CONTENT_CONFLICT,
        metadata={"files": _extract_conflict_files(output)},
    )


def _classify_dirty_working_tree(output: str) -> RebaseErrorKind | None:
    triggers = ["dirty", "uncommitted changes", "local changes", "cannot rebase"]
    if not any(trigger in output for trigger in triggers):
        return None

    return RebaseErrorKind(kind=RebaseKind.DIRTY_WORKING_TREE)


def _classify_concurrent_operation(output: str) -> RebaseErrorKind | None:
    triggers = [
        "rebase in progress",
        "merge in progress",
        "cherry-pick in progress",
        "revert in progress",
        "bisect in progress",
        "Another git process",
        "Locked",
    ]
    if not any(trigger in output for trigger in triggers):
        return None

    operation = _extract_operation(output) or "unknown"
    return RebaseErrorKind(kind=RebaseKind.CONCURRENT_OPERATION, metadata={"operation": operation})


def _classify_repository_corruption(output: str) -> RebaseErrorKind | None:
    triggers = [
        "corrupt",
        "object not found",
        "missing object",
        "invalid object",
        "bad object",
        "disk full",
        "filesystem",
    ]
    if not any(trigger in output for trigger in triggers):
        return None

    return RebaseErrorKind(
        kind=RebaseKind.REPOSITORY_CORRUPT,
        metadata={"details": _extract_error_line(output)},
    )


def _classify_environment_failure(output: str) -> RebaseErrorKind | None:
    triggers = ["user.name", "user.email", "author", "committer", "terminal", "editor"]
    if not any(trigger in output for trigger in triggers):
        return None

    return RebaseErrorKind(
        kind=RebaseKind.ENVIRONMENT_FAILURE,
        metadata={"reason": _extract_error_line(output)},
    )


def _classify_hook_rejection(output: str) -> RebaseErrorKind | None:
    if "pre-rebase" not in output and "hook" not in output and "rejected by" not in output:
        return None

    return RebaseErrorKind(
        kind=RebaseKind.HOOK_REJECTION,
        metadata={"hook_name": _extract_hook_name(output)},
    )


def _classify_content_conflict(output: str) -> RebaseErrorKind | None:
    triggers = ["Conflict", "conflict", "Resolve", "Merge conflict"]
    if not any(trigger in output for trigger in triggers):
        return None

    return RebaseErrorKind(
        kind=RebaseKind.CONTENT_CONFLICT,
        metadata={"files": _extract_conflict_files(output)},
    )


def _classify_patch_failure(output: str) -> RebaseErrorKind | None:
    triggers = ["patch does not apply", "patch failed", "hunk failed", "context mismatch", "fuzz"]
    if not any(trigger in output for trigger in triggers):
        return None

    return RebaseErrorKind(
        kind=RebaseKind.PATCH_APPLICATION_FAILED,
        metadata={"details": _extract_error_line(output)},
    )


def _classify_interactive_stop(output: str) -> RebaseErrorKind | None:
    triggers = ["Stopped at", "paused", "edit command"]
    if not any(trigger in output for trigger in triggers):
        return None

    return RebaseErrorKind(
        kind=RebaseKind.INTERACTIVE_STOP,
        metadata={"command": _extract_command(output)},
    )


def _classify_empty_commit(output: str) -> RebaseErrorKind | None:
    triggers = ["empty", "no changes", "already applied"]
    if not any(trigger in output for trigger in triggers):
        return None

    return RebaseErrorKind(kind=RebaseKind.EMPTY_COMMIT)


def _classify_autostash_failure(output: str) -> RebaseErrorKind | None:
    triggers = ["autostash", "stash"]
    if not any(trigger in output for trigger in triggers):
        return None

    return RebaseErrorKind(
        kind=RebaseKind.AUTOSTASH_FAILED,
        metadata={"details": _extract_error_line(output)},
    )


def _classify_commit_creation_failure(output: str) -> RebaseErrorKind | None:
    triggers = [
        "pre-commit",
        "commit-msg",
        "prepare-commit-msg",
        "post-commit",
        "signing",
        "GPG",
    ]
    if not any(trigger in output for trigger in triggers):
        return None

    return RebaseErrorKind(
        kind=RebaseKind.COMMIT_CREATION_FAILED,
        metadata={"details": _extract_error_line(output)},
    )


def _classify_reference_update_failure(output: str) -> RebaseErrorKind | None:
    triggers = ["cannot lock", "ref update", "packed-refs", "reflog"]
    if not any(trigger in output for trigger in triggers):
        return None

    return RebaseErrorKind(
        kind=RebaseKind.REFERENCE_UPDATE_FAILED,
        metadata={"details": _extract_error_line(output)},
    )


_Classifier = Callable[[str], RebaseErrorKind | None]


_REBASE_CLASSIFIERS: tuple[_Classifier, ...] = (
    _classify_invalid_revision,
    _classify_shallow_or_missing_history,
    _classify_worktree_conflict,
    _classify_submodule_conflict,
    _classify_dirty_working_tree,
    _classify_concurrent_operation,
    _classify_repository_corruption,
    _classify_environment_failure,
    _classify_hook_rejection,
    _classify_content_conflict,
    _classify_patch_failure,
    _classify_interactive_stop,
    _classify_empty_commit,
    _classify_autostash_failure,
    _classify_commit_creation_failure,
    _classify_reference_update_failure,
)


def _extract_revision(output: str) -> str | None:
    patterns = [
        ("invalid revision '", "'"),
        ("unknown revision '", "'"),
        ("bad revision '", "'"),
        ("branch '", "' not found"),
        ("upstream branch '", "' not found"),
        ("revision ", " not found"),
        ("'", "'"),
    ]

    for start, end in patterns:
        start_idx = output.find(start)
        if start_idx == -1:
            continue

        after = output[start_idx + len(start) :]
        end_idx = after.find(end)
        if end_idx == -1:
            continue

        candidate = after[:end_idx]
        if candidate:
            return candidate

    for line in output.splitlines():
        if "not found" not in line and "does not exist" not in line:
            continue

        single_quote_start = line.find("'")
        if single_quote_start != -1:
            next_quote = line.find("'", single_quote_start + 1)
            if next_quote != -1:
                candidate = line[single_quote_start + 1 : next_quote]
                if candidate:
                    return candidate

        double_quote_start = line.find('"')
        if double_quote_start != -1:
            next_quote = line.find('"', double_quote_start + 1)
            if next_quote != -1:
                candidate = line[double_quote_start + 1 : next_quote]
                if candidate:
                    return candidate

    return None


def _extract_operation(output: str) -> str | None:
    mappings = [
        ("rebase in progress", "rebase"),
        ("merge in progress", "merge"),
        ("cherry-pick in progress", "cherry-pick"),
        ("revert in progress", "revert"),
        ("bisect in progress", "bisect"),
    ]

    for pattern, name in mappings:
        if pattern in output:
            return name

    return None


def _extract_hook_name(output: str) -> str:
    hooks = ["pre-rebase", "pre-commit", "commit-msg", "post-commit"]
    for hook in hooks:
        if hook in output:
            return hook
    return "hook"


def _extract_command(output: str) -> str:
    commands = ["edit", "reword", "break", "exec"]
    for command in commands:
        if command in output:
            return command
    return "unknown"


def _extract_error_line(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith("hint:") or lowered.startswith("note:"):
            continue
        return stripped

    return output.strip()


def _extract_conflict_files(output: str) -> list[str]:
    files: list[str] = []
    for line in output.splitlines():
        if not any(keyword in line for keyword in ("CONFLICT", "Conflict", "Merge conflict")):
            continue
        marker = line.find("in ")
        if marker == -1:
            continue
        path = line[marker + 3 :].strip()
        if path:
            files.append(path)
    return files


__all__ = ["RebaseErrorKind", "RebaseKind", "classify_rebase_error"]
