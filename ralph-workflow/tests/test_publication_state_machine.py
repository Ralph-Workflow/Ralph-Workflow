"""Unit tests for the publication state machine (Authority 5)."""

from __future__ import annotations

from typing import Literal

from ralph.pipeline._publication_state_machine import (
    PublicationDecision,
    next_publication_action,
)
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.rebase_state import RebaseState


def _record(phase: Literal["integrating", "integrated"] = "integrated") -> IntegrationRecord:
    return IntegrationRecord(
        phase=phase,
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        integrated_feature_sha="c" * 40,
    )


def test_publication_decision_enum_has_six_values() -> None:
    assert len(PublicationDecision) == 6


def test_publication_terminal_when_no_remote_configured() -> None:
    assert (
        next_publication_action(
            RebaseState(fast_forwarded=True),
            record=_record(),
            remote_unavailable=False,
            configured_remote=None,
        )
        is PublicationDecision.PUBLICATION_TERMINAL
    )


def test_publication_pending_when_record_integrating() -> None:
    assert (
        next_publication_action(
            RebaseState(),
            record=_record(phase="integrating"),
            remote_unavailable=False,
            configured_remote="origin",
        )
        is PublicationDecision.PUBLICATION_PENDING
    )


def test_publication_pending_when_not_fast_forwarded() -> None:
    assert (
        next_publication_action(
            RebaseState(fast_forwarded=False),
            record=_record(),
            remote_unavailable=False,
            configured_remote="origin",
        )
        is PublicationDecision.PUBLICATION_PENDING
    )


def test_publish_deferred_remote_when_remote_unavailable() -> None:
    assert (
        next_publication_action(
            RebaseState(fast_forwarded=True),
            record=_record(),
            remote_unavailable=True,
            configured_remote="origin",
        )
        is PublicationDecision.PUBLISH_DEFERRED_REMOTE
    )


def test_publish_deferred_freshness_when_degraded() -> None:
    assert (
        next_publication_action(
            RebaseState(fast_forwarded=True, freshness_verdict="degraded"),
            record=_record(),
            remote_unavailable=False,
            configured_remote="origin",
        )
        is PublicationDecision.PUBLISH_DEFERRED_FRESHNESS
    )


def test_publication_blocked_when_freshness_unsafe() -> None:
    assert (
        next_publication_action(
            RebaseState(fast_forwarded=True, freshness_verdict="unsafe"),
            record=_record(),
            remote_unavailable=False,
            configured_remote="origin",
        )
        is PublicationDecision.PUBLICATION_BLOCKED
    )


def test_publish_now_when_freshness_verified() -> None:
    assert (
        next_publication_action(
            RebaseState(fast_forwarded=True, freshness_verdict="verified"),
            record=_record(),
            remote_unavailable=False,
            configured_remote="origin",
        )
        is PublicationDecision.PUBLISH_NOW
    )


def test_publish_deferred_freshness_when_freshness_none() -> None:
    assert (
        next_publication_action(
            RebaseState(fast_forwarded=True, freshness_verdict=None),
            record=_record(),
            remote_unavailable=False,
            configured_remote="origin",
        )
        is PublicationDecision.PUBLISH_DEFERRED_FRESHNESS
    )


def test_publish_deferred_freshness_when_freshness_unknown() -> None:
    assert (
        next_publication_action(
            RebaseState(fast_forwarded=True, freshness_verdict="unknown_verdict"),
            record=_record(),
            remote_unavailable=False,
            configured_remote="origin",
        )
        is PublicationDecision.PUBLISH_DEFERRED_FRESHNESS
    )
