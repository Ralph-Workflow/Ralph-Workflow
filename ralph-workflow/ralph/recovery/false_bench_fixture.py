from __future__ import annotations

from collections.abc import Mapping


class FalseBenchFixtureError(ValueError, TypeError):
    pass


def _as_str_object_mapping(value: Mapping[object, object]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise FalseBenchFixtureError("mapping keys must be strings")
        parsed[key] = item
    return parsed


def false_bench_fixture_draft(event: Mapping[str, object]) -> dict[str, object]:
    verdict_ref_raw = event.get("verdict_ref")
    if not isinstance(verdict_ref_raw, Mapping):
        raise FalseBenchFixtureError("event must contain a verdict_ref mapping")
    verdict_ref = _as_str_object_mapping(verdict_ref_raw)

    facts_raw = verdict_ref.get("facts")
    if not isinstance(facts_raw, Mapping):
        raise FalseBenchFixtureError("verdict_ref must contain a facts mapping")
    facts = _as_str_object_mapping(facts_raw)

    session_ref_raw = event.get("session_ref")
    if not isinstance(session_ref_raw, Mapping):
        raise FalseBenchFixtureError("event must contain a session_ref mapping")
    session_ref = _as_str_object_mapping(session_ref_raw)

    attribution_raw = verdict_ref.get("attribution")
    attribution: object | None
    if isinstance(attribution_raw, Mapping):
        attribution = _as_str_object_mapping(attribution_raw).get("attribution")
    else:
        attribution = None

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
