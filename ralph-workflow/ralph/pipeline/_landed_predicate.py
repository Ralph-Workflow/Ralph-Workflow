from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.merge import is_ancestor
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline.auto_integrate_recovery_terminal import RESUME_COMMAND

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord

__all__ = [
    "LandedDecision",
    "expected_landed_facts",
    "operation_landed",
]

_READ_LABEL = "landed-predicate:read"
_PATCH_ID_LABEL = "landed-predicate:patch-id"
_PATCH_ID_PARTS = 2


@dataclass(frozen=True)
class LandedDecision:
    landed: bool
    proof: str
    observed_head: str | None
    expected: tuple[str, ...]
    failures: tuple[str, ...]

    def operator_status(self, preserved: str) -> str:
        return (
            f"observed HEAD {self.observed_head or 'unreadable'}; "
            f"expected {'; '.join(self.expected)}; "
            f"proof failures: {'; '.join(self.failures) or 'none'}; "
            f"preserved copy: {preserved}; "
            f"operator action required: land the expected commit and run '{RESUME_COMMAND}'"
        )


def expected_landed_facts(record: IntegrationRecord) -> tuple[str, ...]:
    return (
        f"integrated_feature_sha={record.integrated_feature_sha or 'unset'}",
        f"pre_target_sha={record.pre_target_sha or 'unset'}",
        f"pre_feature_sha={record.pre_feature_sha}",
        f"rebase_continue_stop={record.rebase_continue_stop or 'unset'}",
        f"rebase_continue_head={record.rebase_continue_head or 'unset'}",
        f"rebase_continue_tree={record.rebase_continue_tree or 'unset'}",
    )


def operation_landed(root: Path, record: IntegrationRecord) -> LandedDecision:
    expected = expected_landed_facts(record)
    head = _read(root, "rev-parse", "--verify", "HEAD")
    if head is None:
        return LandedDecision(False, "none", None, expected, ("HEAD is unreadable",))
    failures: list[str] = []
    proofs = (
        ("advanced integrated_feature_sha", _advanced_completion_proof),
        ("rebased range equivalence", _rebased_range_proof),
        ("verified resolution tree", _resolution_tree_proof),
    )
    for name, proof in proofs:
        landed, detail = proof(root, record, head)
        if landed:
            return LandedDecision(True, name, head, expected, tuple(failures))
        failures.append(detail)
    return LandedDecision(False, "none", head, expected, tuple(failures))


def _advanced_completion_proof(
    root: Path,
    record: IntegrationRecord,
    head: str,
) -> tuple[bool, str]:
    recorded = record.integrated_feature_sha
    if not recorded:
        return False, "integrated_feature_sha is unset"
    if head == recorded:
        return True, ""
    if is_ancestor(root, recorded, head):
        return True, ""
    return (
        False,
        f"HEAD {head} does not descend from the recorded integrated_feature_sha {recorded}",
    )


def _rebased_range_proof(
    root: Path,
    record: IntegrationRecord,
    head: str,
) -> tuple[bool, str]:
    onto = record.pre_target_sha
    feature = record.pre_feature_sha
    if not onto or not feature:
        return False, "the recorded operation range is incomplete"
    if not is_ancestor(root, onto, head):
        return False, f"HEAD {head} does not descend from the recorded onto tip {onto}"
    target_tip = _target_tip(root, record.target) or onto
    rebased = _range_commits(root, f"{target_tip}..{head}")
    original = _range_commits(root, f"{onto}..{feature}")
    range_failure = _range_shape_failure(original, rebased)
    if range_failure is not None:
        return False, range_failure
    original_ids = _patch_ids(root, original)
    rebased_ids = _patch_ids(root, rebased)
    failure = _range_pair_failure(
        record,
        original,
        rebased,
        original_ids,
        rebased_ids,
        root,
        target_tip,
    )
    return (failure is None, failure or "")


def _range_shape_failure(original: tuple[str, ...], rebased: tuple[str, ...]) -> str | None:
    if not original:
        return "the recorded operation range is empty"
    if len(rebased) > len(original):
        return (
            f"the rebased range holds {len(rebased)} commit(s) while the recorded operation "
            f"holds {len(original)}"
        )
    return None


def _range_pair_failure(
    record: IntegrationRecord,
    original: tuple[str, ...],
    rebased: tuple[str, ...],
    original_ids: dict[str, str],
    rebased_ids: dict[str, str],
    root: Path,
    target_tip: str,
) -> str | None:
    upstream = _range_commits(root, target_tip)
    upstream_patch_ids = frozenset(_patch_ids(root, upstream).values())
    replay_index = 0
    for index, old in enumerate(original):
        old_patch = original_ids.get(old)
        if replay_index < len(rebased):
            new = rebased[replay_index]
            if old_patch is not None and old_patch == rebased_ids.get(new):
                replay_index += 1
                continue
            if old == record.rebase_continue_stop and _is_replay_of(root, old, new):
                replay_index += 1
                continue
        if old_patch is not None and old_patch in upstream_patch_ids:
            continue
        return f"commit {index + 1} of the recorded range was neither replayed nor upstream-equivalent"
    if replay_index != len(rebased):
        return "the rebased range contains a commit outside the recorded operation"
    return None


def _resolution_tree_proof(
    root: Path,
    record: IntegrationRecord,
    head: str,
) -> tuple[bool, str]:
    anchor = record.rebase_continue_head
    tree = record.rebase_continue_tree
    if not anchor or not tree:
        return False, "the recorded stop snapshot is incomplete"
    head_tree = _read(root, "rev-parse", "HEAD^{tree}")
    if head == anchor:
        return _anchor_head_tree_result(anchor, tree, head_tree)
    if not is_ancestor(root, anchor, head):
        return False, f"HEAD {head} does not descend from the recorded stop anchor {anchor}"
    if _descendant_carries_tree(root, anchor, head, tree, head_tree):
        return True, ""
    return False, (
        f"no commit descended from the recorded anchor {anchor} carries the verified "
        f"resolution tree {tree}"
    )


def _descendant_carries_tree(
    root: Path,
    anchor: str,
    head: str,
    tree: str,
    head_tree: str | None,
) -> bool:
    return bool(
        _read(root, "rev-parse", f"{anchor}^{{tree}}") == tree
        or _first_commit_carries_tree(root, anchor, head, tree)
        or head_tree == tree
    )


def _anchor_head_tree_result(anchor: str, tree: str, head_tree: str | None) -> tuple[bool, str]:
    if head_tree == tree:
        return True, ""
    return False, (
        f"HEAD is the recorded anchor {anchor} but its tree is not the verified "
        f"resolution tree {tree}"
    )


def _first_commit_carries_tree(root: Path, anchor: str, head: str, tree: str) -> bool:
    first = _first_ancestry_commit(root, anchor, head)
    return bool(
        first is not None
        and _read(root, "rev-parse", f"{first}^{{tree}}") == tree
        and _read(root, "show", "-s", "--format=%P", first) == anchor
    )


def _first_ancestry_commit(root: Path, anchor: str, head: str) -> str | None:
    listing = _read(root, "rev-list", "--reverse", "--ancestry-path", f"{anchor}..{head}")
    if not listing:
        return None
    return listing.splitlines()[0]


def _target_tip(root: Path, target: str) -> str | None:
    return _read(root, "rev-parse", "--verify", f"refs/heads/{target}")


def _range_commits(root: Path, spec: str) -> tuple[str, ...]:
    listing = _read(root, "rev-list", "--reverse", spec)
    if not listing:
        return ()
    return tuple(line for line in listing.splitlines() if line)


def _patch_ids(root: Path, commits: tuple[str, ...]) -> dict[str, str]:
    if not commits:
        return {}
    diff = _read(root, "log", "--no-color", "-p", "--root", "--no-walk", *commits)
    if diff is None:
        return {}
    result = run_git(
        ("patch-id", "--stable"),
        cwd=root,
        label=_PATCH_ID_LABEL,
        options=GitRunOptions(input_data=diff),
    )
    if result.returncode != 0:
        return {}
    identifiers: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= _PATCH_ID_PARTS:
            identifiers[parts[1]] = parts[0]
    return identifiers


def _is_replay_of(root: Path, original: str, replay: str) -> bool:
    original_identity = _commit_identity(root, original)
    replay_identity = _commit_identity(root, replay)
    return original_identity is not None and original_identity == replay_identity


def _commit_identity(root: Path, sha: str) -> str | None:
    line = _read(root, "show", "-s", "--format=%an%x00%ae%x00%at%x00%s", sha)
    if line is None:
        return None
    return line


def _read(root: Path, *args: str) -> str | None:
    result = run_git(args, cwd=root, label=_READ_LABEL)
    if result.returncode != 0:
        return None
    return result.stdout.strip()
