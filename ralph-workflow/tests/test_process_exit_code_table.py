from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from functools import cache
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Mapping

import ralph
from ralph.process.manager import (
    ProcessEvent,
    ProcessRecord,
    ProcessStatus,
    loguru_event_listener,
)

_PACKAGE_DIR = Path(ralph.__file__).resolve().parent
_LABEL_ARGUMENT = re.compile(r"label=(?:f)?[\"']([^\"']*)[\"']")
_SEVERITY_TABLE_PATH = _PACKAGE_DIR / "process" / "manager" / "_process_manager_runtime.py"

_ANSWER_LABELS = (
    "commit:head-symbolic-ref",
    "git-branch-exists",
    "git-branch-sha",
    "integration-observation-head",
    "git-is-ancestor",
    "git-observe-target-sha",
    "git-ort-residue-label",
    "git-ort-residue-tracked",
    "git-rebase:merge-base",
    "git-stray-tracked",
    "git-symbolic-ref-head",
    "hooks-config",
    "integration-ladder:ref-readable",
    "integration-observation-merge-base",
    "integration_diagnostic:check_merge_head",
    "integration_diagnostic:check_rebase_head",
    "integration_diagnostic:check_ancestor",
    "integration_diagnostic:head_parents",
    "integration_diagnostic:gitdir",
    "integration_diagnostic:diff_head",
    "operation_evidence:merge_object_resolvable",
    "operation_evidence:rebase_object_resolvable",
    "planning-residue:ref-exists",
    "recovery:feature-tip-exists",
    "recovery:head-symbolic-ref",
    "repair:commit-controls",
    "repair:proposed-path",
)

_NEW_ANSWER_LABELS = (
    "commit:head-symbolic-ref",
    "git-branch-exists",
    "git-branch-sha",
    "git-observe-target-sha",
    "git-ort-residue-label",
    "git-ort-residue-tracked",
    "git-rebase:merge-base",
    "git-remote-tracking-sha-origin",
    "git-stray-tracked",
    "git-symbolic-ref-head",
    "hooks-config",
    "integration-ladder:ref-readable",
    "integration-observation-merge-base",
    "operation_evidence:merge_object_resolvable",
    "operation_evidence:rebase_object_resolvable",
    "planning-residue:ref-exists",
    "recovery:feature-tip-exists",
    "recovery:head-symbolic-ref",
    "repair:commit-controls",
    "repair:proposed-path",
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_every_emitted_label_has_an_exit_code_decision.absorbed_local_copies": (
        "user requirement: agents always work on the newest code; catch-up absorbs uncommitted "
        "copies that already match the incoming commit instead of staying behind forever, and "
        "its new probes add reviewed labels, so the label-site digest is refreshed"
    ),
    "test_every_emitted_label_has_an_exit_code_decision.interrupted_resolution": (
        "user requirement: an interrupted rebase or merge resolution whose finished edits "
        "are unstaged must go back to the resolver instead of being set aside; the new "
        "mechanical_finish.py probes add reviewed labels, so the label-site digest is refreshed"
    ),
    "test_every_emitted_label_has_an_exit_code_decision.auto_merged_paths": (
        "user requirement: a rebase resolver may repair files both sides changed that git "
        "merged cleanly but wrongly; the new conflict_resolution/auto_merged_paths.py probes "
        "add reviewed labels, so the label-site digest is refreshed"
    ),
    "test_every_emitted_label_has_an_exit_code_decision.git_hooks_path": (
        "user requirement: a commit hook rejection routes back to development; the new "
        "git-hooks-path probe in git/operations.py tells a hook rejection from a git error, "
        "so the reviewed label-site digest is refreshed"
    ),
    "test_every_emitted_label_has_an_exit_code_decision.dirty_landing": (
        "user requirement: committed commits land while the worktree is dirty; the new "
        "pipeline/_dirty_landing.py probes reuse the reviewed git-transition-status and "
        "git-symbolic-ref-head labels, so the reviewed label-site digest is refreshed"
    ),
    "test_every_emitted_label_has_an_exit_code_decision.publication_gate_syntax": (
        "the reviewed process-label inventory now includes the publication gate's sh -n "
        "syntax check, so the pinned digest of emitted labels changes"
    ),
    "test_every_emitted_label_has_an_exit_code_decision.detached_head_branch_tip": (
        "the reviewed process-label inventory now includes the detached-HEAD branch-tip "
        "probe, so the pinned digest of emitted labels changes"
    ),
    "test_every_emitted_label_has_an_exit_code_decision.shared_config_guard": (
        "the reviewed process-label inventory now includes the shared git config guard's "
        "restore label, so the pinned digest of emitted labels changes"
    ),
    "test_every_emitted_label_has_an_exit_code_decision.publication_gate": (
        "the reviewed process-label inventory now includes the publication gate's command "
        "label and its tree probe, so the pinned digest of emitted labels changes"
    ),
    "test_every_emitted_label_has_an_exit_code_decision.policy_round_set_aside": (
        "the reviewed process-label inventory now includes the policy round landing's "
        "stash push and pop, which set development work aside while a remediation round "
        "lands; a failure of either is a real error, so the pinned digest of emitted labels "
        "changes"
    ),
    "test_every_emitted_label_has_an_exit_code_decision.lint_bypass_diff": (
        "the reviewed process-label inventory now includes commit review's lint-bypass diff, "
        "a git diff without --exit-code whose only answer is rc=0, so the pinned digest of "
        "emitted labels changes"
    ),
    "_git_probe_answer_codes skips modules that never name run_git before parsing": (
        "the scan only records run_git calls, and a module whose source does not contain run_git cannot hold one, so skipping it before ast.parse yields the same answer-code table while cutting about 3 s of collection"
    ),
    "test_every_emitted_label_has_an_exit_code_decision": (
        "the reviewed inventory gains recovery:feature-tip-exists, a cat-file -e probe whose "
        "rc=1 means the recorded feature tip is absent and is therefore an expected answer; "
        "prior notes still cover the integration-preservation reapply labels and the retired "
        "recovery:recorded-identity-status and hardening:porcelain-z probes"
    ),
    "test_every_git_probe_label_covers_the_answer_exit_codes_of_its_commands": (
        "the new cell derives every git probe label and the command it runs from the "
        "package source, classifies the exit codes git uses as answers for that command "
        "and requires the expected-exit table to cover them, so a benign git answer is "
        "never logged as a process failure"
    ),
    "test_unexpected_exit_code_stays_a_warning": (
        "the exit codes git uses as answers for a probe are excluded from the loud check "
        "of that probe, because a widened answer set turns those codes into expected "
        "answers instead of process failures"
    ),
}

_NON_BENIGN_LABEL_INVENTORY_DIGEST = (
    "ba385c9801b1c542d6adcf7bc751d08b86cff9f9036b5bcee68f7e86e79a30c1"
)
_NON_BENIGN_LABEL_INVENTORY_REVIEW = (
    "The reviewed inventory adds catchup-blob-of (rev-parse --verify --quiet of a path at a "
    "commit while deciding whether an uncommitted copy already matches the incoming version; "
    "rc=1 is git's answer that the path does not exist there), catchup-hash-object, "
    "catchup-absorb-local-copy, catchup-restore-local-copy and catchup-unstage-local-copy (a "
    "failure is a real error), integration-resolution-rebase-head, "
    "integration-resolution-merge-head and integration-resolution-merge-base "
    "(rev-parse --verify --quiet of REBASE_HEAD or MERGE_HEAD and merge-base while "
    "checking an interrupted resolution for unstaged edits; rc=1 is git's answer that the "
    "ref or common ancestor does not exist), integration-resolution-replayed-paths, "
    "integration-resolution-merged-paths and integration-resolution-unstaged-edits (a "
    "failure is a real error), git-auto-merged-rev-parse and git-auto-merged-merge-base "
    "(rev-parse --verify --quiet and merge-base while listing the paths both sides of a "
    "replayed rebase commit changed; rc=1 is git's answer that the revision or common "
    "ancestor does not exist), git-auto-merged-diff-tree (a failure is a real error), "
    "git-hooks-path (rev-parse --git-path hooks after a "
    "failed commit, to tell a commit hook rejection from a git error; rc=128 is git's "
    "answer outside a repository), publication-gate-syntax (sh -n of the declared gate "
    "command; rc=2 is the shell's answer that the declaration is not a command), "
    "git-detached-head-branch-tip (rev-parse --verify of a "
    "candidate branch for a detached HEAD; rc=128 is git's answer when that branch does not "
    "exist), shared-config-guard:restore (git config --file on the "
    "shared config after an agent attempt broke core.bare or core.worktree; any nonzero "
    "exit stays loud), publication-gate-tree (rev-parse of the tree being "
    "published; rc=128 is git's answer for an unreadable commit) and publication-gate "
    "(the project's verify command; a nonzero exit is a red gate and stays loud), and "
    "recovery:feature-tip-exists (cat-file -e of a recorded "
    "feature tip; rc=1 is an expected answer when the tip is gone) alongside the prior "
    "replacements for git-rebase:diff, operation-snapshot:index-tree, "
    "auto-integrate:update-feature-ref, git-completion-in-place, git-append-only-attributes, "
    "git-rm-resolved, recovery:completed-rebase-*, pending-rebase:land-*, and "
    "integration-preservation:reapply-work-diff*. Retired probes recovery:recorded-identity-status "
    "and hardening:porcelain-z remain absent. pipeline/_dirty_landing.py reuses "
    "git-transition-status (status of the run worktree) and git-symbolic-ref-head (rc=1 is "
    "git's answer for a detached HEAD). Real recovery and conflict-resolution operations "
    "whose nonzero exits are not answers keep the loud default."
)

_GIT_SUBCOMMANDS: Final[frozenset[str]] = frozenset(
    {
        "cat-file",
        "config",
        "diff",
        "log",
        "ls-files",
        "merge-base",
        "rev-list",
        "rev-parse",
        "show",
        "show-ref",
        "symbolic-ref",
    }
)

_GIT_GLOBAL_OPTIONS_WITH_VALUE: Final[frozenset[str]] = frozenset(
    {
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--work-tree",
        "-c",
    }
)

_GIT_CONFIG_MUTATIONS: Final[frozenset[str]] = frozenset(
    {
        "--add",
        "--remove-section",
        "--rename-section",
        "--replace-all",
        "--unset",
        "--unset-all",
    }
)

_GIT_ANSWER_RULES: Final[
    Mapping[str, tuple[tuple[frozenset[str], frozenset[str], frozenset[int]], ...]]
] = {
    "rev-parse": (
        (frozenset({"--verify", "--quiet"}), frozenset(), frozenset({1})),
        (frozenset({"--verify"}), frozenset({"--quiet"}), frozenset({128})),
        (frozenset(), frozenset({"--verify"}), frozenset({128})),
    ),
    "show-ref": (
        (frozenset({"--quiet"}), frozenset(), frozenset({1})),
        (frozenset(), frozenset({"--quiet"}), frozenset({128})),
    ),
    "cat-file": (
        (frozenset({"-e"}), frozenset(), frozenset({1})),
        (frozenset({"blob"}), frozenset(), frozenset({128})),
    ),
    "merge-base": ((frozenset(), frozenset(), frozenset({1})),),
    "symbolic-ref": ((frozenset({"--quiet"}), frozenset(), frozenset({1})),),
    "ls-files": ((frozenset({"--error-unmatch"}), frozenset(), frozenset({1})),),
    "show": ((frozenset(), frozenset(), frozenset({128})),),
    "config": ((frozenset(), _GIT_CONFIG_MUTATIONS, frozenset({1})),),
    "log": ((frozenset(), frozenset(), frozenset({128})),),
    "rev-list": ((frozenset({"--max-count"}), frozenset(), frozenset({128})),),
}


def _emitted_levels(label: str, returncode: int) -> list[str]:
    levels: list[str] = []
    sink_id = logger.add(
        lambda message: levels.append(str(message).strip()),
        level="DEBUG",
        format="{level}",
    )
    try:
        record = ProcessRecord(
            pid=4242,
            pgid=4242,
            command=("git", "symbolic-ref", "--quiet", "--", "HEAD"),
            cwd=None,
            started_at=datetime.now(tz=UTC),
            status=ProcessStatus.EXITED,
            returncode=returncode,
            label=label,
        )
        event = ProcessEvent(
            record=record,
            previous_status=ProcessStatus.RUNNING,
            new_status=ProcessStatus.EXITED,
            timestamp=datetime.now(tz=UTC),
        )
        loguru_event_listener(event)
    finally:
        logger.remove(sink_id)
    return levels


def _git_subcommand(argv: tuple[str, ...]) -> str | None:
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "*":
            index += 1
            continue
        if token in _GIT_GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token if token in _GIT_SUBCOMMANDS else None
    return None


def _mentions_revision(argv: tuple[str, ...]) -> bool:
    return any(
        token == "HEAD"
        or "^{" in token
        or (len(token) == 40 and all(character in "0123456789abcdefABCDEF" for character in token))
        for token in argv
    )


def _diff_answer_exit_codes(argv: tuple[str, ...]) -> frozenset[int]:
    flags = frozenset(argv)
    if "--quiet" in flags:
        return frozenset({1, 128})
    if "--no-index" in flags:
        return frozenset({1})
    return frozenset({128}) if _mentions_revision(argv) else frozenset()


def _git_answer_exit_codes(argv: tuple[str, ...]) -> frozenset[int]:
    command = _git_subcommand(argv)
    if command is None:
        return frozenset()
    if command == "diff":
        return _diff_answer_exit_codes(argv)
    flags = frozenset(argv)
    for required, forbidden, codes in _GIT_ANSWER_RULES.get(command, ()):
        if required <= flags and not (forbidden & flags):
            return codes
    return frozenset()


def _probe_arguments(node: ast.expr) -> tuple[str, ...] | None:
    if not isinstance(node, ast.Tuple | ast.List):
        return None
    arguments: list[str] = []
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            arguments.append(element.value)
        elif isinstance(element, ast.Starred):
            arguments.append("*")
        else:
            arguments.append("{}")
    return tuple(arguments)


def _probe_label(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [
            value.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
            else "{}"
            for value in node.values
        ]
        return "".join(parts)
    return None


def _label_keyword(call: ast.Call) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == "label":
            return keyword.value
    return None


@cache
def _git_probe_answer_codes() -> dict[str, frozenset[int]]:
    answers: dict[str, set[int]] = {}
    for path in sorted(_PACKAGE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if "label=" not in source or "run_git" not in source:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if name != "run_git":
                continue
            arguments = _probe_arguments(node.args[0])
            label_node = _label_keyword(node)
            if arguments is None or label_node is None:
                continue
            codes = _git_answer_exit_codes(arguments)
            label = _probe_label(label_node)
            if not codes or label is None:
                continue
            answers.setdefault(label, set()).update(codes)
    return {label: frozenset(codes) for label, codes in answers.items()}


@cache
def _production_expected_exit_codes() -> dict[str, frozenset[int]]:
    source = _SEVERITY_TABLE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_SEVERITY_TABLE_PATH))
    table: dict[str, frozenset[int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign | ast.Assign):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "EXPECTED_EXIT_CODES"
            for target in targets
        ):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key, entry in zip(node.value.keys, node.value.values, strict=True):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                continue
            codes = entry.args[0] if isinstance(entry, ast.Call) else entry
            if not isinstance(codes, ast.Set | ast.Tuple | ast.List):
                continue
            table[key.value] = frozenset(
                element.value
                for element in codes.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, int)
            )
    return table


def _pattern_matches(pattern: str, key: str) -> bool:
    if key.endswith("*"):
        prefix = key[:-1]
        head = pattern.split("{}", maxsplit=1)[0]
        return head.startswith(prefix) or prefix.startswith(head)
    expression = ".*".join(re.escape(part) for part in pattern.split("{}"))
    return re.fullmatch(expression, key) is not None


_GIT_PROBE_ANSWER_CODES: Final[Mapping[str, frozenset[int]]] = _git_probe_answer_codes()
_PRODUCTION_EXPECTED_EXIT_CODES: Final[Mapping[str, frozenset[int]]] = (
    _production_expected_exit_codes()
)


@pytest.mark.parametrize("label", _ANSWER_LABELS)
def test_expected_exit_code_is_debug_and_not_warning(label: str) -> None:
    levels = _emitted_levels(label, 1)

    assert levels == ["DEBUG"], f"label={label} rc=1 must be a quiet answer, got {levels}"


@pytest.mark.parametrize("label", _ANSWER_LABELS)
@pytest.mark.parametrize("returncode", [2, 128])
def test_unexpected_exit_code_stays_a_warning(label: str, returncode: int) -> None:
    if returncode in _GIT_PROBE_ANSWER_CODES.get(label, frozenset()):
        return
    levels = _emitted_levels(label, returncode)

    assert "WARNING" in levels, f"label={label} rc={returncode} must warn, got {levels}"
    assert "DEBUG" not in levels, f"label={label} rc={returncode} must not be quiet, got {levels}"


@pytest.mark.parametrize("label", _NEW_ANSWER_LABELS)
def test_new_expected_exit_codes_are_debug_only_for_one(label: str) -> None:
    assert _emitted_levels(label, 1) == ["DEBUG"]
    assert _emitted_levels(label, 2) == ["WARNING"]
    assert _emitted_levels(label, 128) == ["WARNING"]


def test_unregistered_label_keeps_the_loud_default() -> None:
    levels = _emitted_levels("some-unregistered-probe", 1)

    assert "WARNING" in levels, f"unknown labels must warn, got {levels}"
    assert "DEBUG" not in levels, f"unknown labels must not be quiet, got {levels}"


def test_git_rebase_continue_keeps_the_loud_default() -> None:
    levels = _emitted_levels("git-rebase:continue", 1)

    assert "WARNING" in levels, (
        "git rebase --continue exits 1 when the index still holds unresolved "
        f"conflicts, which is a real failure, so it must warn; got {levels}"
    )
    assert "DEBUG" not in levels, f"git-rebase:continue rc=1 must not be quiet, got {levels}"


def test_unlabeled_process_keeps_the_loud_default() -> None:
    levels = _emitted_levels("", 1)

    assert "WARNING" in levels, f"unlabeled processes must warn, got {levels}"
    assert "DEBUG" not in levels, f"unlabeled processes must not be quiet, got {levels}"


def test_successful_exit_emits_nothing() -> None:
    levels = _emitted_levels("integration-observation-head", 0)

    assert levels == [], f"a successful probe must stay silent, got {levels}"


@cache
def _emitted_label_patterns() -> tuple[re.Pattern[str], ...]:
    patterns: list[re.Pattern[str]] = []
    for path in sorted(_PACKAGE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for literal in _LABEL_ARGUMENT.findall(source):
            if "{" in literal:
                pieces = literal.split("{")
                head = pieces[0]
                tail = pieces[-1].split("}", 1)[-1]
                patterns.append(re.compile(f"{re.escape(head)}.*{re.escape(tail)}"))
            else:
                patterns.append(re.compile(re.escape(literal)))
    return tuple(patterns)


@cache
def _process_label_source_digest() -> str:
    entries: list[str] = []
    for path in sorted(_PACKAGE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(_PACKAGE_DIR).as_posix()
        entries.extend(f"{relative}:{literal}" for literal in _LABEL_ARGUMENT.findall(source))
    return sha256("\n".join(sorted(entries)).encode()).hexdigest()


def test_every_emitted_label_has_an_exit_code_decision() -> None:
    assert _process_label_source_digest() == _NON_BENIGN_LABEL_INVENTORY_DIGEST, (
        _NON_BENIGN_LABEL_INVENTORY_REVIEW
    )


@pytest.mark.parametrize("label", _ANSWER_LABELS)
def test_registered_label_is_emitted_by_the_package(label: str) -> None:
    patterns = _emitted_label_patterns()

    assert any(pattern.fullmatch(label) for pattern in patterns), (
        f"label {label!r} is never passed as a process label, so the "
        "expected-exit table entry would never apply"
    )


def test_every_git_probe_label_covers_the_answer_exit_codes_of_its_commands() -> None:
    probes = _GIT_PROBE_ANSWER_CODES
    table = _PRODUCTION_EXPECTED_EXIT_CODES

    assert probes, "no git probe label was derived from the package source"

    for label, codes in sorted(probes.items()):
        if "{" in label:
            matching = [key for key in table if _pattern_matches(label, key)]
            assert matching, f"git probe pattern {label!r} has no expected-exit decision"
            assert any(codes <= table[key] for key in matching), (
                f"git probe pattern {label!r} answers with {sorted(codes)} but no row "
                f"covering it exists among {sorted(matching)}"
            )
            continue
        assert label in table, f"git probe label {label!r} has no expected-exit decision"
        assert codes <= table[label], (
            f"git probe label {label!r} answers with {sorted(codes)} but the expected-exit "
            f"table allows {sorted(table[label])}"
        )


@pytest.mark.parametrize("label", sorted(_GIT_PROBE_ANSWER_CODES))
def test_a_git_probe_answer_exit_code_is_quiet(label: str) -> None:
    if "{" in label:
        pytest.skip("a formatted label has no single concrete instance to probe")
    for code in sorted(_GIT_PROBE_ANSWER_CODES[label]):
        levels = _emitted_levels(label, code)
        assert levels == ["DEBUG"], (
            f"label={label} rc={code} is git's answer and must be quiet, got {levels}"
        )
