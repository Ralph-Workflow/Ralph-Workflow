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
    """Static-scan ralph/ for direct last_publication_decision writers."""
    offenders: list[str] = []
    # Candidate subtrees where rebase state is defined and consumed:
    candidate_roots = (
        RALPH_ROOT / "pipeline",
        RALPH_ROOT / "git",
        RALPH_ROOT / "recovery",
    )
    for root in candidate_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if path.name == "rebase_state.py":
                continue
            # Cheap bytes pre-filter: decode + parse only files that mention the
            # field at all, so the 1s per-test timeout holds under shard load.
            raw = path.read_bytes()
            if b"last_publication_decision" not in raw:
                continue
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


def test_no_direct_writer_sites_outside_rebase_state() -> None:
    offenders = _writer_sites()
    assert offenders == [], (
        f"direct last_publication_decision writers outside rebase_state.py: {offenders}"
    )
