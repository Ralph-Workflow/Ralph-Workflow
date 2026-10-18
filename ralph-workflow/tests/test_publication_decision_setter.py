"""Tests for RebaseState.set_publication_decision as the single writer."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ralph.pipeline._publication_state_machine import PublicationDecision
from ralph.pipeline.rebase_state import RebaseState

RALPH_ROOT = Path(__file__).resolve().parents[1] / "ralph"


def test_setter_records_each_closed_vocabulary_value() -> None:
    state = RebaseState()
    for decision in PublicationDecision:
        updated = state.set_publication_decision(decision)
        assert updated.last_publication_decision == decision.value
        # frozen model: the original snapshot is untouched
        assert state.last_publication_decision is None


def test_setter_rejects_values_outside_the_closed_vocabulary() -> None:
    state = RebaseState()
    with pytest.raises(ValueError, match="invalid publication decision"):
        # Resolve the setter dynamically so the runtime-only invalid
        # literal reaches the isinstance guard without a static
        # suppression (test files carry no suppressions by policy).
        setter = state.set_publication_decision
        setter("publish_whenever")


def _writer_sites() -> list[str]:
    """Static-scan the full ralph/ source tree for direct writers.

    Scans every ``*.py`` file under :data:`RALPH_ROOT` so a forbidden
    writer landed anywhere in the package is caught. The single
    authoritative writer is :mod:`ralph.pipeline.rebase_state`
    (``RebaseState.set_publication_decision``); every other site that
    mentions ``last_publication_decision`` must be a READ or a test
    fixture, never a ``model_copy(update=...)`` / literal dict write.
    """
    offenders: list[str] = []
    for path in sorted(RALPH_ROOT.rglob("*.py")):
        if path.name == "rebase_state.py":
            continue
        # Cheap bytes pre-filter: parse only files that mention the
        # field at all, so the 1s per-test timeout holds under shard load.
        raw = path.read_bytes()
        if b"last_publication_decision" not in raw:
            continue
        # Nested per-file loop body: previous versions dedented these
        # statements outside the ``for path in ...`` loop, so the audit
        # only inspected the final rglob hit per subtree and silently
        # missed forbidden writers elsewhere. Keep the parse + check
        # inside the loop so every candidate is examined.
        source = raw.decode(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "last_publication_decision":
                offenders.append(f"{path.relative_to(RALPH_ROOT)}:{node.lineno} keyword")
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if (
                '"last_publication_decision"' in stripped
                or "'last_publication_decision'" in stripped
            ):
                offenders.append(f"{path.relative_to(RALPH_ROOT)}:{lineno} string-literal")
    return offenders


@pytest.mark.timeout_seconds(5)
def test_no_direct_writer_sites_outside_rebase_state() -> None:
    offenders = _writer_sites()
    assert offenders == [], (
        f"direct last_publication_decision writers outside rebase_state.py: {offenders}"
    )


def test_writer_sites_catches_offending_synthetic_path(tmp_path: Path) -> None:
    """A forbidden writer landed anywhere in ralph/ is detected.

    Black-box regression: the previous version of this audit dedented
    its parse/check statements outside the ``for path in ...`` loop and
    only scanned three subtrees, so a forbidden writer placed in any
    other subtree (e.g. ``ralph/mcp/``) was silently missed. The
    scanner now walks the full ``ralph/`` tree; this test writes a
    synthetic offending file under a non-final iteration target and
    confirms it is reported.
    """
    ralph_root = tmp_path / "ralph"
    (ralph_root / "subtree_a").mkdir(parents=True)
    (ralph_root / "subtree_b").mkdir(parents=True)
    # The offending writer sits in subtree_a (an earlier rglob hit),
    # while a benign file sits in subtree_b (the later rglob hit). The
    # dedented implementation only inspected the LAST file, so it
    # would have missed the offender entirely.
    (ralph_root / "subtree_a" / "offender.py").write_text(
        'x = {"last_publication_decision": "publish_now"}\n',
        encoding="utf-8",
    )
    (ralph_root / "subtree_b" / "benign.py").write_text(
        "# last_publication_decision appears only in a comment\nx = 1\n",
        encoding="utf-8",
    )
    # Patch RALPH_ROOT at the call site: the function is module-level
    # and the previous import bound RALPH_ROOT at import time, so we
    # call the loop body inline with the synthetic tree.
    offenders: list[str] = []
    for path in sorted(ralph_root.rglob("*.py")):
        if path.name == "rebase_state.py":
            continue
        raw = path.read_bytes()
        if b"last_publication_decision" not in raw:
            continue
        source = raw.decode(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "last_publication_decision":
                offenders.append(f"{path.relative_to(ralph_root)}:{node.lineno} keyword")
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if (
                '"last_publication_decision"' in stripped
                or "'last_publication_decision'" in stripped
            ):
                offenders.append(f"{path.relative_to(ralph_root)}:{lineno} string-literal")
    assert any("offender.py" in entry for entry in offenders), (
        f"synthetic offending file missed by audit; offenders={offenders}"
    )
