from __future__ import annotations

import re
from datetime import UTC, datetime
from functools import cache
from hashlib import sha256
from pathlib import Path

import pytest
from loguru import logger

import ralph
from ralph.process.manager import (
    ProcessEvent,
    ProcessRecord,
    ProcessStatus,
    loguru_event_listener,
)

_PACKAGE_DIR = Path(ralph.__file__).resolve().parent
_LABEL_ARGUMENT = re.compile(r"label=(?:f)?[\"']([^\"']*)[\"']")

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
    "recovery:head-symbolic-ref",
    "repair:commit-controls",
    "repair:proposed-path",
)

_NON_BENIGN_LABEL_INVENTORY_DIGEST = (
    "2335d85e2838c8f7bc6ba9863cedddb6dfa3e525cca51cc130477f3fc196f117"
)
_NON_BENIGN_LABEL_INVENTORY_REVIEW = (
    "The reviewed inventory replaces git-rebase:diff with the four "
    "git-rebase:set-aside-* operations, removes the retired integration-gate:* "
    "probe, and adds operation-snapshot:index-tree, auto-integrate:update-feature-ref, "
    "git-completion-in-place, and git-append-only-attributes. These are real recovery "
    "and conflict-resolution operations whose nonzero exits must retain the loud default."
)


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


@pytest.mark.parametrize("label", _ANSWER_LABELS)
def test_expected_exit_code_is_debug_and_not_warning(label: str) -> None:
    levels = _emitted_levels(label, 1)

    assert levels == ["DEBUG"], f"label={label} rc=1 must be a quiet answer, got {levels}"


@pytest.mark.parametrize("label", _ANSWER_LABELS)
@pytest.mark.parametrize("returncode", [2, 128])
def test_unexpected_exit_code_stays_a_warning(label: str, returncode: int) -> None:
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
