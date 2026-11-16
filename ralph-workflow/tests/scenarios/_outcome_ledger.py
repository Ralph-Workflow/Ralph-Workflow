from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

__all__ = [
    "EMPTY_OUTCOME_LEDGER",
    "OutcomeLedger",
    "driver_session_outcome",
    "key_landed_outcome",
    "outcome_entry",
    "record_outcome",
]


class OutcomeLedger:
    def __init__(self) -> None:
        self.entries: dict[str, str] = {}

    def put(self, key: str, value: str) -> None:
        self.entries[key] = value

    def read(self, key: str) -> str:
        value = self.entries.get(key)
        if value is None:
            raise AssertionError(f"durable outcome ledger has no entry {key!r}")
        return value


EMPTY_OUTCOME_LEDGER: str = "{}"


def record_outcome(
    ledger: OutcomeLedger,
    head: str,
    target: str,
    anchors: tuple[str, ...],
    trees: tuple[str, ...],
) -> str:
    verdict = key_landed_outcome(head, target, anchors, trees)
    ledger.put("landed-verdict", verdict)
    return verdict


def key_landed_outcome(
    head: str, target: str, anchors: tuple[str, ...], trees: tuple[str, ...]
) -> str:
    anchor_ancestors = ", ".join(anchors)
    tree_matches = ", ".join(trees)
    if (
        head == target
        or any(anchor == head for anchor in anchors)
        or any(tree == head for tree in trees)
    ):
        return "landed"
    return f"not-landed head={head} target={target} anchors=({anchor_ancestors}) trees=({tree_matches})"


def outcome_entry(ledger: OutcomeLedger, key: str) -> str:
    return ledger.read(key)


def driver_session_outcome(session: ResolutionSession) -> str:
    outcome = getattr(session, "last_resolution_outcome", None)
    if outcome is None:
        raise AssertionError("resolution session has no terminal outcome recorded")
    return type(outcome).__name__
