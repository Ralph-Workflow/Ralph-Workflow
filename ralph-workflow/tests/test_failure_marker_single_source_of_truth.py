from __future__ import annotations

from ralph.pipeline import effect_executor, retryable_failure
from ralph.recovery import failure_classifier


def test_post_tool_activity_markers_are_single_sourced() -> None:
    assert (
        retryable_failure.POST_TOOL_ACTIVITY_MARKERS
        is failure_classifier.POST_TOOL_ACTIVITY_MARKERS
    )


def test_post_tool_empty_response_markers_are_single_sourced() -> None:
    assert (
        retryable_failure.POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS
        is failure_classifier.POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS
    )


def test_session_not_found_substrings_are_single_sourced() -> None:
    assert (
        retryable_failure.SESSION_NOT_FOUND_SUBSTRINGS
        is failure_classifier.SESSION_NOT_FOUND_SUBSTRINGS
    )


def test_effect_executor_does_not_redefine_shared_markers() -> None:
    for name in (
        "_POST_TOOL_ACTIVITY_MARKERS",
        "_POST_TOOL_EMPTY_RESPONSE_MARKERS",
        "_TURN_LIMIT_MARKERS",
        "_TRANSIENT_CONNECTIVITY_MARKERS",
    ):
        assert not hasattr(effect_executor, name), (
            f"effect_executor still defines {name}; it is dead duplicated state"
        )


def test_transient_connectivity_set_is_intentionally_broader_than_transport() -> None:
    transient = {m.casefold() for m in retryable_failure._TRANSIENT_CONNECTIVITY_MARKERS}
    transport = {m.casefold() for m in failure_classifier._TRANSPORT_SUBSTRINGS}
    assert "timeout" in transient
    assert "timeout" not in transport
    assert (
        retryable_failure._TRANSIENT_CONNECTIVITY_MARKERS
        is not failure_classifier._TRANSPORT_SUBSTRINGS
    )
