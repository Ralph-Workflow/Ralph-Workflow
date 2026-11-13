from __future__ import annotations

from collections.abc import Mapping
from typing import cast


def false_bench_fixture_draft(event: Mapping[str, object]) -> dict[str, object]:
    verdict_ref = event.get("verdict_ref")
    if not isinstance(verdict_ref, Mapping):
        raise ValueError("event must contain a verdict_ref mapping")
    verdict_ref = cast("Mapping[str, object]", verdict_ref)

    facts = verdict_ref.get("facts")
    if not isinstance(facts, Mapping):
        raise ValueError("verdict_ref must contain a facts mapping")
    facts = cast("Mapping[str, object]", facts)

    session_ref = event.get("session_ref")
    if not isinstance(session_ref, Mapping):
        raise ValueError("event must contain a session_ref mapping")
    session_ref = cast("Mapping[str, object]", session_ref)

    attribution = verdict_ref.get("attribution")
    if isinstance(attribution, Mapping):
        attribution = cast("Mapping[str, object]", attribution).get("attribution")

    fixture: dict[str, object] = {
        "id": f"false_bench_{event['agent']}_{event['config_fingerprint']}",
        "label": "negative",
        "expected_bench": False,
        "expected_verdict": "not_agent_fault",
        "expected_attribution": attribution,
        "agent": event["agent"],
        "transport": event["transport"],
    }
    fixture.update(facts)
    fixture.update(
        {
            "termination": verdict_ref["termination"],
            "captured_lines": list[str](),
            "source_session": dict[str, object](session_ref),
            "notes": ("A reviewer fills captured_lines from source_session before committing."),
        }
    )
    return fixture
