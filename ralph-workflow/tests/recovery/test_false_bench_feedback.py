from ralph.pipeline.run_time_report import render_run_time_report
from ralph.pipeline.state import PipelineState
from ralph.recovery.agent_config_fingerprint import agent_config_fingerprint
from ralph.recovery.false_bench_feedback import FalseBenchFeedback


def _refs() -> tuple[dict[str, object], dict[str, str]]:
    return {"version": 2, "facts": {}}, {
        "run": "run-1",
        "phase": "development",
        "entry_point": "main",
        "session_id": "session-1",
    }


def test_matching_success_counts_false_bench_with_evidence() -> None:
    feedback = FalseBenchFeedback()
    verdict_ref, session_ref = _refs()
    feedback.note_bench(
        "development",
        "codex",
        "opaque reason",
        config_fingerprint="abc123",
        verdict_ref=verdict_ref,
        session_ref=session_ref,
    )

    assert feedback.note_success("review", "codex", config_fingerprint="abc123") is True
    assert feedback.count == 1
    assert feedback.events == (
        {
            "agent": "codex",
            "phase": "development",
            "reason": "opaque reason",
            "config_fingerprint": "abc123",
            "verdict_ref": verdict_ref,
            "session_ref": session_ref,
        },
    )
    assert feedback.note_success("review", "codex", config_fingerprint="abc123") is False


def test_different_fingerprint_does_not_close_candidate() -> None:
    feedback = FalseBenchFeedback()
    verdict_ref, session_ref = _refs()
    feedback.note_bench(
        "development",
        "codex",
        "opaque reason",
        config_fingerprint="first",
        verdict_ref=verdict_ref,
        session_ref=session_ref,
    )

    assert feedback.note_success("review", "codex", config_fingerprint="second") is False
    assert feedback.count == 0
    assert feedback.note_success("review", "codex", config_fingerprint="first") is True


def test_success_without_bench_counts_nothing() -> None:
    feedback = FalseBenchFeedback()

    assert feedback.note_success("review", "codex", config_fingerprint="abc123") is False
    assert feedback.count == 0
    assert feedback.events == ()


def test_snapshot_round_trip_preserves_count_and_open_candidates() -> None:
    feedback = FalseBenchFeedback()
    verdict_ref, session_ref = _refs()
    for agent in ("codex", "claude"):
        feedback.note_bench(
            "development",
            agent,
            "opaque reason",
            config_fingerprint=f"fp-{agent}",
            verdict_ref=verdict_ref,
            session_ref=session_ref,
        )
    assert feedback.note_success("review", "codex", config_fingerprint="fp-codex") is True

    restored = FalseBenchFeedback.from_snapshot(feedback.snapshot())

    assert restored.count == 1
    assert restored.events == feedback.events
    assert restored.note_success("review", "claude", config_fingerprint="fp-claude") is True
    assert FalseBenchFeedback.from_snapshot(None).count == 0
    assert FalseBenchFeedback.from_snapshot({"old": "state"}).count == 0


def test_events_are_bounded_to_most_recent_32() -> None:
    feedback = FalseBenchFeedback()
    verdict_ref, session_ref = _refs()
    for index in range(33):
        fingerprint = f"fp-{index}"
        feedback.note_bench(
            "development",
            "codex",
            str(index),
            config_fingerprint=fingerprint,
            verdict_ref=verdict_ref,
            session_ref=session_ref,
        )
        assert feedback.note_success("review", "codex", config_fingerprint=fingerprint)

    assert feedback.count == 33
    assert len(feedback.events) == 32
    assert feedback.events[0]["reason"] == "1"


def test_agent_config_fingerprint_is_stable_non_secret_and_model_sensitive() -> None:
    arguments = {
        "agent": "codex",
        "cmd": "/usr/local/bin/codex --token secret-one",
        "model": "model-a",
        "model_flag": "--model",
        "json_parser": "codex-json",
        "transport": "subprocess",
    }
    first = agent_config_fingerprint(**arguments)

    assert first == agent_config_fingerprint(**arguments)
    assert len(first) == 16
    assert first != agent_config_fingerprint(**{**arguments, "model": "model-b"})
    assert first == agent_config_fingerprint(
        **{**arguments, "cmd": "/usr/local/bin/codex --token another-secret"}
    )


def test_old_pipeline_state_loads_and_report_shows_false_benches() -> None:
    old_state = PipelineState.model_validate({"phase": "development"})
    assert old_state.false_bench_feedback is None
    state = PipelineState(
        phase="development",
        false_bench_feedback={
            "count": 1,
            "events": [
                {
                    "agent": "codex",
                    "phase": "development",
                    "reason": "opaque reason",
                    "config_fingerprint": "abc123",
                    "verdict_ref": {},
                    "session_ref": {},
                }
            ],
            "candidates": [],
        },
    )

    report = render_run_time_report(state=state, outcome="completed", elapsed_seconds=1)

    assert "False benches: 1 (codex@development [abc123])" in report
    assert "False benches:" not in render_run_time_report(
        state=old_state, outcome="completed", elapsed_seconds=1
    )
