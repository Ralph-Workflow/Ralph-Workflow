"""Regression tests for the Evidence Provenance trust lattice (F1/F3, DoD #6).

Covers:

- The ``Provenance`` ordering and the ``Evidence`` type invariants (S-1).
- ``grade_verdict`` requiring every fact at ``WIRE`` to reach ``PASS`` (S-1).
- ``_transport_evidence_ceiling`` reporting a ceiling below ``WIRE`` for a
  transport whose ``init`` frame advertises no route to Ralph's tools (S-3).
- A regression pinning a captured on-disk AGY transcript's grading to
  ``DEGRADED (host-synthesized)`` — the exact shape of run that previously
  printed ``Breaks: none`` (S-5, DoD #6).
"""

from __future__ import annotations

import json

import pytest

from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.pipeline.plumbing.smoke_evidence import (
    DEGRADED,
    PASS,
    Evidence,
    Provenance,
    absent,
    format_verdict,
    grade_verdict,
)
from ralph.pipeline.plumbing.smoke_plumbing import transport_evidence_ceiling


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
    (promoted -> ``WORKSPACE_EFFECT``), the harness synthesized the
    completion sentinel itself because AGY never called ``declare_complete``
    (-> ``HOST_SYNTHESIZED``), and the transcript showed 14 frames with zero
    ``tools/call`` records (-> ``TRANSCRIPT``, since no wire-ledger match
    exists). That run printed ``File: yes / Artifact: yes / Breaks: none``
    under the old boolean contract. Under the lattice it must grade exactly
    ``DEGRADED (host-synthesized)`` -- the weakest of the three required
    facts -- and can never grade ``PASS``.
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
            holds=True,
            provenance=Provenance.HOST_SYNTHESIZED,
            detail="written by the harness (AGY fallback-artifact completion synthesis)",
        ),
    }

    label, weakest = grade_verdict(required_facts)

    assert label == DEGRADED
    assert weakest == Provenance.HOST_SYNTHESIZED
    assert format_verdict(required_facts) == "DEGRADED (host-synthesized)"
    assert label != PASS, "the 2026-08-05 run must never grade PASS or print 'Breaks: none'"
