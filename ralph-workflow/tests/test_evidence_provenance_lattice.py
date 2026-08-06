"""Regression tests for the Evidence Provenance trust lattice (F1/F3, DoD #6).

Covers:

- The ``Provenance`` ordering and the ``Evidence`` type invariants (S-1).
- ``grade_verdict`` requiring every fact at ``WIRE`` to reach ``PASS`` (S-1).
- ``_transport_evidence_ceiling`` reporting a ceiling below ``WIRE`` for a
  transport whose ``init`` frame advertises no route to Ralph's tools (S-3).
- A regression pinning a captured on-disk AGY transcript's grading to
  ``DEGRADED (host-synthesized)`` — the exact shape of run that previously
  printed ``Breaks: none`` (S-5, DoD #6).
- An end-to-end replay of that same 2026-08-05 shape through the real
  ``_run_smoke_agent`` harness path, proving the grading functions
  themselves derive ``DEGRADED (host-synthesized)`` from a transcript —
  not just that ``grade_verdict`` arithmetic is correct in isolation
  (Evidence Provenance closeout plan, S-1).
"""

from __future__ import annotations

import json
from collections import deque
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import InvokeOptions
from ralph.cli.commands.smoke import _required_evidence
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.plumbing.smoke_evidence import (
    DEGRADED,
    PASS,
    Evidence,
    Provenance,
    absent,
    format_verdict,
    grade_verdict,
)
from ralph.pipeline.plumbing.smoke_plumbing import (
    SmokeRunParams,
    _run_smoke_agent,
    transport_evidence_ceiling,
)
from tests._support.mock_agy import (
    DEGRADED_BASELINE_RUN_ID,
    degraded_baseline_artifact_markdown,
    degraded_baseline_stream_json_lines,
)

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch


def test_provenance_ordering_is_least_to_most_trustworthy() -> None:
    assert (
        Provenance.ABSENT
        < Provenance.HOST_SYNTHESIZED
        < Provenance.WORKSPACE_EFFECT
        < Provenance.TRANSCRIPT
        < Provenance.WIRE
    )


def test_evidence_cannot_hold_true_with_absent_provenance() -> None:
    with pytest.raises(ValueError, match=r"Provenance\.ABSENT"):
        Evidence(holds=True, provenance=Provenance.ABSENT, detail="bogus")


def test_evidence_requires_a_provenance_member() -> None:
    non_provenance_value: object = "WIRE"
    with pytest.raises(TypeError):
        Evidence(holds=True, provenance=non_provenance_value, detail="bogus")


def test_absent_helper_returns_canonical_non_holding_evidence() -> None:
    ev = absent("nothing to see here")
    assert ev.holds is False
    assert ev.provenance is Provenance.ABSENT
    assert ev.detail == "nothing to see here"


def test_grade_verdict_requires_every_fact_at_wire_for_pass() -> None:
    all_wire = {
        "a": Evidence(True, Provenance.WIRE, "x"),
        "b": Evidence(True, Provenance.WIRE, "y"),
    }
    label, weakest = grade_verdict(all_wire)
    assert label == PASS
    assert weakest == Provenance.WIRE


def test_grade_verdict_demotes_to_degraded_when_any_fact_below_wire() -> None:
    mixed = {
        "a": Evidence(True, Provenance.WIRE, "x"),
        "b": Evidence(True, Provenance.TRANSCRIPT, "y"),
    }
    label, weakest = grade_verdict(mixed)
    assert label == DEGRADED
    assert weakest == Provenance.TRANSCRIPT


def test_grade_verdict_reports_the_single_weakest_provenance_overall() -> None:
    facts = {
        "a": Evidence(True, Provenance.WORKSPACE_EFFECT, "x"),
        "b": Evidence(True, Provenance.TRANSCRIPT, "y"),
        "c": Evidence(True, Provenance.HOST_SYNTHESIZED, "z"),
    }
    label, weakest = grade_verdict(facts)
    assert label == DEGRADED
    assert weakest == Provenance.HOST_SYNTHESIZED


def test_grade_verdict_never_passes_when_a_fact_does_not_hold() -> None:
    facts = {
        "a": Evidence(True, Provenance.WIRE, "x"),
        "b": absent("missing"),
    }
    label, weakest = grade_verdict(facts)
    assert label == DEGRADED
    assert weakest == Provenance.ABSENT


def test_grade_verdict_empty_mapping_grades_degraded_absent() -> None:
    label, weakest = grade_verdict({})
    assert label == DEGRADED
    assert weakest == Provenance.ABSENT


def test_format_verdict_pass_has_no_parenthetical() -> None:
    assert format_verdict({"a": Evidence(True, Provenance.WIRE, "x")}) == "PASS"


def test_format_verdict_degraded_names_the_weakest_provenance() -> None:
    facts = {"a": Evidence(True, Provenance.HOST_SYNTHESIZED, "x")}
    assert format_verdict(facts) == "DEGRADED (host-synthesized)"


# --- S-3: transport evidence ceiling -----------------------------------


def _agy_config() -> AgentConfig:
    return AgentConfig(cmd="agy", transport=AgentTransport.AGY)


def _init_frame(tool_names: list[str]) -> str:
    return json.dumps(
        {
            "event": "init",
            "conversation_id": "00000000-0000-0000-0000-000000000001",
            "init": {
                "cwd": "/workspace",
                "tools": tool_names,
                "permission_mode": "always-proceed",
            },
        }
    )


def test_ceiling_reports_below_wire_when_no_ralph_tool_advertised() -> None:
    """The measured AGY v1.1.10 shape: 56 tools, 0 ``ralph_*``."""
    tool_names = [
        "ask_permission",
        "ask_question",
        "define_subagent",
        "invoke_subagent",
        "manage_subagents",
        "view_file",
        "write_to_file",
        "grep_search",
        "run_command",
    ]
    lines = [_init_frame(tool_names)]

    ceiling = transport_evidence_ceiling(_agy_config(), lines)

    assert ceiling < Provenance.WIRE
    assert ceiling == Provenance.TRANSCRIPT


def test_ceiling_reports_wire_when_a_ralph_tool_is_advertised() -> None:
    lines = [_init_frame(["view_file", "ralph_submit_md_artifact", "write_to_file"])]

    assert transport_evidence_ceiling(_agy_config(), lines) == Provenance.WIRE


def test_ceiling_reports_wire_when_call_mcp_tool_dispatcher_is_advertised() -> None:
    lines = [_init_frame(["view_file", "call_mcp_tool", "write_to_file"])]

    assert transport_evidence_ceiling(_agy_config(), lines) == Provenance.WIRE


def test_ceiling_reports_absent_when_no_init_frame_present() -> None:
    lines = [json.dumps({"event": "step_update", "step_update": {"step_index": 0}})]

    assert transport_evidence_ceiling(_agy_config(), lines) == Provenance.ABSENT


def test_ceiling_ignores_non_json_and_malformed_lines() -> None:
    lines = [
        "plain text banner",
        "{not valid json",
        _init_frame(["view_file"]),
    ]

    assert transport_evidence_ceiling(_agy_config(), lines) == Provenance.TRANSCRIPT


# --- S-5: regression pinning the measured DEGRADED (host-synthesized) run --


def test_2026_08_05_run_grades_degraded() -> None:
    """Pin the exact measured-run scenario from the Evidence Provenance brief.

    The 2026-08-05 baseline run: the AGY agent wrote a fallback artifact
    (promoted -> ``WORKSPACE_EFFECT``), the agent never called
    ``declare_complete`` (-> completion stays ``ABSENT`` post-F7, since the
    host no longer fabricates the sentinel), and the transcript showed 14
    frames with zero ``tools/call`` records (-> ``TRANSCRIPT``, since no
    wire-ledger match exists). That run printed ``File: yes / Artifact: yes
    / Breaks: none`` under the old boolean contract. Under the lattice it
    must grade exactly ``DEGRADED (absent)`` -- the weakest of the three
    required facts -- and can never grade ``PASS``.
    """
    required_facts = {
        "artifact_submitted": Evidence(
            holds=True,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail="promoted the fallback file .agent/tmp/smoke_test_result.md",
        ),
        "tool_activity_seen": Evidence(
            holds=True,
            provenance=Provenance.TRANSCRIPT,
            detail="14 frames, 0 tools/call",
        ),
        "explicit_completion_seen": Evidence(
            holds=False,
            provenance=Provenance.ABSENT,
            detail="completion sentinel was not observed",
        ),
    }

    label, weakest = grade_verdict(required_facts)

    assert label == DEGRADED
    assert weakest == Provenance.ABSENT
    assert format_verdict(required_facts) == "DEGRADED (absent)"
    assert label != PASS, "the 2026-08-05 run must never grade PASS or print 'Breaks: none'"


# --- Evidence Provenance closeout plan, S-1: end-to-end transcript replay --


def test_2026_08_05_transcript_replay_grades_degraded_host_synthesized(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Replay the reconstructed 2026-08-05 shape through the REAL harness path.

    ``test_2026_08_05_run_grades_degraded`` above hand-constructs three
    ``Evidence`` objects directly and proves the lattice arithmetic
    (``grade_verdict``) is right. It does not prove the grading *functions*
    (``_artifact_submission_evidence``, ``_completion_evidence``,
    ``_tool_activity_evidence``, ``transport_evidence_ceiling``) actually
    derive those three provenances from a transcript shaped like the real
    run. This test closes that gap: it feeds
    ``tests._support.mock_agy.degraded_baseline_stream_json_lines`` (a
    reconstruction of the measured 2026-08-05 shape -- see that function's
    docstring and ``tests/display/_fixtures/agy_wire_provenance.md`` for the
    provenance note) through ``_run_smoke_agent`` via the same
    monkeypatched-``execute_agent_effect`` pattern
    ``tests/test_smoke_plumbing_uses_canonical_submit.py`` uses, then grades
    the resulting ``SmokeRunResult`` through the real
    ``grade_verdict(_required_evidence(result))`` path -- not a
    hand-assembled mapping.
    """
    monkeypatch.delenv("RALPH_BROKER_SECRET", raising=False)

    config = _agy_config()
    output_dir = tmp_path / "tmp" / "interactive-agy-smoke"
    output_dir.mkdir(parents=True)
    output_file = output_dir / "todo-list.js"
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("smoke prompt", encoding="utf-8")
    params = SmokeRunParams(
        agent_name="agy/gemini-3.6-flash-low",
        config=config,
        unified_config=UnifiedConfig(general=GeneralConfig()),
        workspace_root=tmp_path,
        prompt_file=prompt_file,
        output_file=output_file,
        options=InvokeOptions(),
        display_context=make_display_context(),
        bridge=object(),
        pipeline_deps=object(),
    )
    run_id = DEGRADED_BASELINE_RUN_ID
    artifact_path = tmp_path / ".agent" / "tmp" / "smoke_test_result.md"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.extend(degraded_baseline_stream_json_lines())
        # The write_to_file tool call in the transcript above is a real
        # workspace effect (matches the measured run's "File created" fact).
        output_file.write_text("// smoke output\n", encoding="utf-8")
        # Artifact reaches disk only via fallback promotion: no route to
        # ``ralph_submit_md_artifact`` existed, so the agent wrote the
        # fallback markdown directly instead, per the brief's own quoted
        # transcript text.
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(degraded_baseline_artifact_markdown(), encoding="utf-8")
        # CRUCIALLY: no completion sentinel is written here -- the agent
        # never called declare_complete either, matching the measured run.
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    # The transport ceiling is derived from the init frame, not asserted --
    # the measured AGY shape (no ralph_*/call_mcp_tool route) caps below WIRE.
    assert result.transport_evidence_ceiling == Provenance.TRANSCRIPT

    # Each required fact is derived by the real grading functions, not
    # hand-assigned -- pin the exact provenance each one actually reached.
    assert result.artifact_submitted.holds is True
    assert result.artifact_submitted.provenance == Provenance.WORKSPACE_EFFECT
    # Post-F7/DoD 19, the host writes no completion evidence for any
    # transport; the measured 2026-08-05 AGY run did not call
    # ``declare_complete``, so ``explicit_completion_seen`` is now
    # ``ABSENT`` (not ``HOST_SYNTHESIZED``).
    assert result.explicit_completion_seen.holds is False
    assert result.explicit_completion_seen.provenance == Provenance.ABSENT
    assert result.tool_activity_seen.holds is True
    assert result.tool_activity_seen.provenance == Provenance.TRANSCRIPT

    # The overall verdict is derived through the same path the CLI report
    # uses -- grade_verdict(_required_evidence(result)) -- not asserted
    # directly against a hand-built mapping.
    required_facts = _required_evidence(result)
    label, weakest = grade_verdict(required_facts)

    assert label == DEGRADED
    assert weakest == Provenance.ABSENT
    assert format_verdict(required_facts) == "DEGRADED (absent)"
    assert label != PASS, "the 2026-08-05 run must never grade PASS or print 'Breaks: none'"
