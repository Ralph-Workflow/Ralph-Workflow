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
    "landed_status",
    "operation_landed",
]

_READ_LABEL = "landed-predicate:read"
_PATCH_ID_LABEL = "landed-predicate:patch-id"
_PATCH_ID_PARTS = 2
_GITLINK_MODE = "160000"
_GITLINK_ENTRY_FIELDS = 3


@dataclass(frozen=True)
class LandedDecision:
    landed: bool
    proof: str
    observed_head: str | None
    expected: tuple[str, ...]
    failures: tuple[str, ...]
    on_target: bool = False

    @property
    def landed_on_target(self) -> bool:
        return self.landed and self.on_target

    def operator_status(self, preserved: str) -> str:
        return landed_status(
            observed_head=self.observed_head,
            expected=self.expected,
            failures=self.failures,
            preserved=preserved,
        )


@dataclass(frozen=True)
class _LiveGitFacts:
    head: str
    head_tree: str | None
    index_tree: str | None


def landed_status(
    *,
    observed_head: str | None,
    expected: tuple[str, ...],
    failures: tuple[str, ...],
    preserved: str,
) -> str:
    return (
        f"observed HEAD {observed_head or 'unreadable'}; "
        f"expected {'; '.join(expected)}; "
        f"proof failures: {'; '.join(failures) or 'none'}; "
        f"preserved copy: {preserved}; "
        f"land the expected commit; resume point: {RESUME_COMMAND}"
    )


def expected_landed_facts(record: IntegrationRecord) -> tuple[str, ...]:
    return (
        f"integrated_feature_sha={record.integrated_feature_sha or 'unset'}",
        f"pre_target_sha={record.pre_target_sha or 'unset'}",
        f"pre_feature_sha={record.pre_feature_sha or 'unset'}",
        f"rebase_continue_stop={record.rebase_continue_stop or 'unset'}",
        f"rebase_continue_head={record.rebase_continue_head or 'unset'}",
        f"rebase_continue_tree={record.rebase_continue_tree or 'unset'}",
        f"merge_commit_parent={record.merge_commit_parent or 'unset'}",
    )


def operation_landed(root: Path, record: IntegrationRecord) -> LandedDecision:
    expected = expected_landed_facts(record)
    head = _read(root, "rev-parse", "--verify", "HEAD")
    if head is None:
        return LandedDecision(
            landed=False,
            proof="none",
            observed_head=None,
            expected=expected,
            failures=("HEAD is unreadable",),
        )
    facts = _LiveGitFacts(
        head=head,
        head_tree=_read(root, "rev-parse", "HEAD^{tree}"),
        index_tree=_read(root, "write-tree"),
    )
    failures: list[str] = []
    proofs = (
        ("advanced integrated_feature_sha", _advanced_completion_proof),
        ("rebased range equivalence", _rebased_range_proof),
        ("verified resolution tree", _resolution_tree_proof),
    )
    for name, proof in proofs:
        landed, detail = proof(root, record, facts)
        if landed:
            target_tip = _target_tip(root, record.target)
            return LandedDecision(
                landed=True,
                proof=name,
                observed_head=facts.head,
                expected=expected,
                failures=tuple(failures),
                on_target=bool(target_tip and is_ancestor(root, facts.head, target_tip)),
            )
        failures.append(detail)
    return LandedDecision(
        landed=False,
        proof="none",
        observed_head=facts.head,
        expected=expected,
        failures=tuple(failures),
    )


def _advanced_completion_proof(
    root: Path,
    record: IntegrationRecord,
    facts: _LiveGitFacts,
) -> tuple[bool, str]:
    if record.is_integrating and record.integrated_feature_sha == record.pre_feature_sha:
        return False, "feature has not advanced beyond pre_feature_sha"
    recorded = record.integrated_feature_sha
    if not recorded:
        return False, "integrated_feature_sha is unset"
    if facts.head == recorded:
        return True, ""
    if record.is_integrated and is_ancestor(root, recorded, facts.head):
        return True, ""
    return (
        False,
        f"HEAD {facts.head} does not descend from the recorded integrated_feature_sha {recorded}",
    )


def _rebased_range_proof(
    root: Path,
    record: IntegrationRecord,
    facts: _LiveGitFacts,
) -> tuple[bool, str]:
    onto = record.pre_target_sha
    feature = record.pre_feature_sha
    if not onto or not feature:
        return False, "the recorded operation range is incomplete"
    if not is_ancestor(root, onto, facts.head):
        return False, f"HEAD {facts.head} does not descend from the recorded onto tip {onto}"
    target_tip = _target_tip(root, record.target) or onto
    if facts.head == feature and (not target_tip or not is_ancestor(root, facts.head, target_tip)):
        return (
            False,
            f"HEAD is the unintegrated feature commit {facts.head} and has not landed on {record.target}",
        )
    base = target_tip if target_tip and is_ancestor(root, target_tip, facts.head) else onto
    rebased = _range_commits(root, f"{base}..{facts.head}")
    original = _range_commits(root, f"{onto}..{feature}")
    if not original and not rebased and target_tip and is_ancestor(root, facts.head, target_tip):
        return True, ""
    range_failure = _range_shape_failure(original, rebased)
    if range_failure is not None:
        return False, range_failure
    original_ids = _patch_ids(root, original)
    rebased_ids = _patch_ids(root, rebased)
    return _range_equivalence_verdict(
        record,
        original,
        rebased,
        original_ids,
        rebased_ids,
        root,
        target_tip,
        facts,
    )


def _range_equivalence_verdict(
    record: IntegrationRecord,
    original: tuple[str, ...],
    rebased: tuple[str, ...],
    original_ids: dict[str, str],
    rebased_ids: dict[str, str],
    root: Path,
    target_tip: str,
    facts: _LiveGitFacts,
) -> tuple[bool, str]:
    pair_failure = _range_pair_failure(
        record,
        original,
        rebased,
        original_ids,
        rebased_ids,
        root,
        target_tip,
        facts,
    )
    if pair_failure is None:
        return True, ""
    set_failure = _range_set_equivalence_failure(
        record,
        original,
        rebased,
        original_ids,
        rebased_ids,
        root,
        target_tip,
        facts,
    )
    if set_failure is None:
        return True, ""
    return False, f"{pair_failure}; {set_failure}"


def _live_state_failure(facts: _LiveGitFacts) -> str | None:
    if facts.index_tree is None or facts.head_tree is None:
        return None
    if facts.index_tree != facts.head_tree:
        return "the live index tree differs from HEAD's tree; the operation left pending state"
    return None


def _resolution_tree_proof(
    root: Path,
    record: IntegrationRecord,
    facts: _LiveGitFacts,
) -> tuple[bool, str]:
    anchor = record.rebase_continue_head
    tree = record.rebase_continue_tree
    if not anchor or not tree:
        return False, "the recorded stop snapshot is incomplete"
    failure = _resolution_anchor_failure(root, record, facts, anchor, tree)
    if failure is None:
        failure = _resolution_content_failure(root, facts, anchor, tree)
    return (failure is None, failure or "")


def _resolution_anchor_failure(
    root: Path,
    record: IntegrationRecord,
    facts: _LiveGitFacts,
    anchor: str,
    tree: str,
) -> str | None:
    onto = record.pre_target_sha
    if not onto:
        return "the recorded operation base is unknown"
    if not _is_live_commit(root, anchor) or not _is_live_tree(root, tree):
        return "the recorded stop snapshot does not name live git objects"
    if not is_ancestor(root, onto, anchor):
        return f"the recorded stop anchor {anchor} is outside the recorded operation"
    if not is_ancestor(root, anchor, facts.head):
        return f"HEAD {facts.head} does not descend from the recorded stop anchor {anchor}"
    return None


def _resolution_content_failure(
    root: Path,
    facts: _LiveGitFacts,
    anchor: str,
    tree: str,
) -> str | None:
    anchor_tree = _read(root, "rev-parse", f"{anchor}^{{tree}}")
    if anchor_tree == tree:
        return "the recorded resolution tree carries no change over the recorded anchor"
    state_failure = _live_state_failure(facts)
    if state_failure is not None:
        return state_failure
    if not _descendant_carries_tree(root, anchor, facts.head, tree, facts.head_tree):
        return (
            f"no commit descended from the recorded anchor {anchor} carries the verified "
            f"resolution tree {tree}"
        )
    return None


def _descendant_carries_tree(
    root: Path,
    anchor: str,
    head: str,
    tree: str,
    head_tree: str | None,
) -> bool:
    return bool(_first_commit_carries_tree(root, anchor, head, tree) or head_tree == tree)


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


def _range_shape_failure(original: tuple[str, ...], rebased: tuple[str, ...]) -> str | None:
    if not original:
        return "the recorded operation range is empty"
    if len(rebased) > len(original):
        return (
            f"the rebased range holds {len(rebased)} commit(s) while the recorded operation "
            f"holds {len(original)}"
        )
    return None


def _stop_resolved_to_empty(
    root: Path,
    record: IntegrationRecord,
    facts: _LiveGitFacts,
) -> bool:
    anchor = record.rebase_continue_head
    tree = record.rebase_continue_tree
    onto = record.pre_target_sha
    if not (anchor and tree and onto):
        return False
    if not _is_live_commit(root, anchor) or not _is_live_tree(root, tree):
        return False
    if not is_ancestor(root, anchor, facts.head):
        return False
    if not is_ancestor(root, onto, anchor):
        return False
    anchor_tree = _read(root, "rev-parse", f"{anchor}^{{tree}}")
    return bool(anchor_tree and anchor_tree == tree)


def _range_pair_failure(
    record: IntegrationRecord,
    original: tuple[str, ...],
    rebased: tuple[str, ...],
    original_ids: dict[str, str],
    rebased_ids: dict[str, str],
    root: Path,
    target_tip: str,
    facts: _LiveGitFacts,
) -> str | None:
    upstream = _range_commits(root, target_tip)
    upstream_patch_ids = frozenset(_patch_ids(root, upstream).values())
    stop_is_empty = _stop_resolved_to_empty(root, record, facts)
    replay_index = 0
    for index, old in enumerate(original):
        old_patch = original_ids.get(old)
        if replay_index < len(rebased):
            new = rebased[replay_index]
            if old_patch is not None and old_patch == rebased_ids.get(new):
                replay_index += 1
                continue
            if _is_replay_of(root, old, new):
                replay_index += 1
                continue
        if old_patch is not None and old_patch in upstream_patch_ids:
            continue
        if old == record.rebase_continue_stop and stop_is_empty:
            continue
        if _policy_dropped_gitlink(root, old, facts.head, target_tip):
            continue
        return (
            f"commit {index + 1} of the recorded range was neither replayed nor upstream-equivalent"
        )
    if replay_index != len(rebased):
        return "the rebased range contains a commit outside the recorded operation"
    return None


def _range_set_equivalence_failure(
    record: IntegrationRecord,
    original: tuple[str, ...],
    rebased: tuple[str, ...],
    original_ids: dict[str, str],
    rebased_ids: dict[str, str],
    root: Path,
    target_tip: str,
    facts: _LiveGitFacts,
) -> str | None:
    upstream = _range_commits(root, target_tip)
    upstream_patch_ids = frozenset(_patch_ids(root, upstream).values())
    stop_is_empty = _stop_resolved_to_empty(root, record, facts)
    remaining_rebased = list(rebased)
    rebased_pids = dict(rebased_ids)

    for old in original:
        old_pid = original_ids.get(old)
        if old_pid is not None and old_pid in upstream_patch_ids:
            continue
        if old == record.rebase_continue_stop and stop_is_empty:
            continue
        if _policy_dropped_gitlink(root, old, facts.head, target_tip):
            continue
        matched_idx: int | None = None
        for idx, new in enumerate(remaining_rebased):
            new_pid = rebased_pids.get(new)
            if old_pid is not None and old_pid == new_pid:
                matched_idx = idx
                break
            if _is_replay_of(root, old, new):
                matched_idx = idx
                break
        if matched_idx is not None:
            remaining_rebased.pop(matched_idx)
        else:
            return f"non-excluded feature change {old[:8]} is absent from rebased range"

    if remaining_rebased:
        return "unrelated target change is attributed to this integration"
    return None


def _policy_dropped_gitlink(
    root: Path,
    original: str,
    landed_head: str,
    target_tip: str,
) -> bool:
    paths = _changed_paths(root, original)
    if not paths:
        return False
    for path in paths:
        if not _is_gitlink_path(root, original, path):
            return False
        original_pointer = _gitlink_pointer(root, original, path)
        target_pointer = _gitlink_pointer(root, target_tip, path)
        landed_pointer = _gitlink_pointer(root, landed_head, path)
        if original_pointer is None or target_pointer is None or landed_pointer is None:
            return False
        if target_pointer == original_pointer:
            return False
        if landed_pointer != target_pointer:
            return False
    return True


def _changed_paths(root: Path, commit: str) -> tuple[str, ...]:
    listing = _read(root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit)
    if not listing:
        return ()
    return tuple(line for line in listing.splitlines() if line)


def _is_gitlink_path(root: Path, commit: str, path: str) -> bool:
    entry = _read(root, "ls-tree", commit, "--", path)
    if not entry:
        return False
    return entry.split(maxsplit=1)[0] == _GITLINK_MODE


def _gitlink_pointer(root: Path, commit: str, path: str) -> str | None:
    entry = _read(root, "ls-tree", commit, "--", path)
    if not entry:
        return None
    parts = entry.split()
    if len(parts) < _GITLINK_ENTRY_FIELDS or parts[0] != _GITLINK_MODE:
        return None
    return parts[2]


def _is_live_commit(root: Path, sha: str) -> bool:
    return _read(root, "cat-file", "-t", sha) == "commit"


def _is_live_tree(root: Path, sha: str) -> bool:
    return _read(root, "cat-file", "-t", sha) == "tree"


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
