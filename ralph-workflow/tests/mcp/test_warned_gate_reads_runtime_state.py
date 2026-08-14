"""The incomplete-work gate must fire on runtime state, not on self-declaration.

The gate that requires an ``## Incomplete Work`` section from a warned
``partial``/``failed`` development result used to key on a frontmatter flag the
reporting agent wrote itself, so an agent could clear the gate by simply not
writing the flag. That is the exact failure the gate exists to prevent, so the
runtime's own published cycle deadline is authoritative: when the pipeline has
already warned, the gate fires whatever the frontmatter says.
"""

from __future__ import annotations

import time

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import DEVELOPMENT_RESULT_SPEC
from ralph.mcp.protocol.env import CYCLE_DEADLINE_EPOCH_ENV, CYCLE_WARN_EPOCH_ENV

#: Epochs are published against the wall clock the validating process reads, so
#: the fixtures below straddle the real "now" rather than a frozen instant.
_MARGIN_SECONDS = 600.0

_UNFLAGGED_WITHOUT_INCOMPLETE_WORK = """---
type: development_result
status: partial
---
## Summary
- [SUM-1] Ran out of time before finishing S-4.
"""

_UNFLAGGED_WITH_INCOMPLETE_WORK = """---
type: development_result
status: partial
---
## Summary
- [SUM-1] Completed S-1 through S-3; S-4 interrupted by the deadline.
## Incomplete Work
- [S-4] Rename API endpoints: half-applied.
  Reason: Only half the endpoints were renamed before the deadline.
  Evidence: tests/test_api.py:42 still references the old endpoint names.
"""


@pytest.fixture
def cycle_already_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publish a deadline whose warning point is already behind us."""
    now = time.time()
    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, repr(now - _MARGIN_SECONDS))
    monkeypatch.setenv(CYCLE_DEADLINE_EPOCH_ENV, repr(now + _MARGIN_SECONDS))


@pytest.fixture
def cycle_not_yet_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publish a deadline whose warning point is still ahead."""
    now = time.time()
    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, repr(now + _MARGIN_SECONDS))
    monkeypatch.setenv(CYCLE_DEADLINE_EPOCH_ENV, repr(now + 2 * _MARGIN_SECONDS))


@pytest.fixture
def no_deadline_published(monkeypatch: pytest.MonkeyPatch) -> None:
    """No cycle timebox is configured for this run."""
    monkeypatch.delenv(CYCLE_WARN_EPOCH_ENV, raising=False)
    monkeypatch.delenv(CYCLE_DEADLINE_EPOCH_ENV, raising=False)


@pytest.mark.usefixtures("cycle_already_warned")
def test_a_warned_cycle_gates_a_partial_that_omits_the_flag() -> None:
    """Omitting the self-declared flag no longer clears the gate."""
    _content, diagnostics = parse_and_validate(
        _UNFLAGGED_WITHOUT_INCOMPLETE_WORK, DEVELOPMENT_RESULT_SPEC
    )

    assert any("Incomplete Work" in diagnostic.message for diagnostic in diagnostics)


@pytest.mark.usefixtures("cycle_already_warned")
def test_a_warned_cycle_accepts_an_honest_report_without_the_flag() -> None:
    """The runtime-driven gate is satisfied by the report, not by the flag."""
    content, diagnostics = parse_and_validate(
        _UNFLAGGED_WITH_INCOMPLETE_WORK, DEVELOPMENT_RESULT_SPEC
    )

    assert diagnostics == []
    # The stable-ID prefix is only attached on the warned branch, so it proves
    # the gate ran rather than the section merely being carried best-effort.
    assert content["incomplete_work"] == ["[S-4] Rename API endpoints: half-applied."]


@pytest.mark.usefixtures("cycle_already_warned")
def test_a_warned_cycle_still_validates_each_incomplete_item() -> None:
    """A warned report cannot degrade to items without a reason."""
    _content, diagnostics = parse_and_validate(
        """---
type: development_result
status: partial
---
## Summary
- [SUM-1] S-4 incomplete.
## Incomplete Work
- [S-4] Rename API endpoints: half-applied.
  Evidence: tests/test_api.py:42 references the old endpoint names.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert any("Reason" in diagnostic.message for diagnostic in diagnostics)


@pytest.mark.usefixtures("cycle_not_yet_warned")
def test_an_unwarned_cycle_leaves_an_ordinary_partial_alone() -> None:
    """A cycle inside its budget imposes no incomplete-work requirement."""
    content, diagnostics = parse_and_validate(
        _UNFLAGGED_WITHOUT_INCOMPLETE_WORK, DEVELOPMENT_RESULT_SPEC
    )

    assert diagnostics == []
    assert content["status"] == "partial"


@pytest.mark.usefixtures("no_deadline_published")
def test_a_run_without_a_timebox_imposes_no_requirement() -> None:
    """No published deadline means the gate cannot fire at all."""
    _content, diagnostics = parse_and_validate(
        _UNFLAGGED_WITHOUT_INCOMPLETE_WORK, DEVELOPMENT_RESULT_SPEC
    )

    assert diagnostics == []


@pytest.mark.usefixtures("no_deadline_published")
def test_a_declared_flag_gates_without_a_published_deadline() -> None:
    """A result validated outside its warned invocation keeps the strict read.

    Replays and hand-written reports reach validation with the environment
    already withdrawn, so the declared flag stays meaningful rather than being
    superseded by the runtime probe.
    """
    _content, diagnostics = parse_and_validate(
        """---
type: development_result
status: partial
cycle_timebox_warned: true
---
## Summary
- [SUM-1] Ran out of time before finishing S-4.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert any("Incomplete Work" in diagnostic.message for diagnostic in diagnostics)


@pytest.mark.usefixtures("cycle_already_warned")
def test_a_warned_completion_claim_must_carry_its_proof() -> None:
    """The one word the agent controls must not be the way past the gate.

    Gating only ``partial``/``failed`` meant the honest report was the one that
    got rejected while a fabricated ``completed`` — two one-character items and
    no proof at all — sailed through, which is the exact outcome the gate was
    written to prevent.
    """
    _content, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Everything is done.
## Files Changed
- [F-1] src/feature.py
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert any("Plan Items Proven" in diagnostic.message for diagnostic in diagnostics)


@pytest.mark.usefixtures("cycle_already_warned")
def test_a_warned_completion_with_proof_is_accepted() -> None:
    """A completion claim backed by per-item proof is exactly what is wanted."""
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Landed S-1 and S-2 with tests.
## Files Changed
- [F-1] src/feature.py
## Plan Items Proven
- [S-1] Added the endpoint rename and updated its callers.
  Disposition: completed
  Rationale: Renamed every call site the plan item listed.
- [S-2] Covered the rename with tests/test_api.py::test_renamed_endpoint.
  Disposition: completed
  Rationale: The new test fails against the old endpoint names.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert len(content["plan_items_proven"]) == 2


@pytest.mark.usefixtures("cycle_not_yet_warned")
def test_an_unwarned_completion_is_not_asked_for_proof() -> None:
    """Outside a warned cycle the proof section stays optional, as declared."""
    _content, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Everything is done.
## Files Changed
- [F-1] src/feature.py
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []


@pytest.mark.usefixtures("cycle_already_warned")
@pytest.mark.parametrize(
    "fields",
    [
        pytest.param("  - Reason: Tests not updated.\n  - Evidence: tests/test_api.py:42\n", id="nested-bullets"),
        pytest.param("  **Reason:** Tests not updated.\n  **Evidence:** tests/test_api.py:42\n", id="bold-labels"),
        pytest.param("  Reason: Tests not updated.\n  Evidence: tests/test_api.py:42\n", id="bare"),
    ],
)
def test_incomplete_work_fields_are_read_in_the_shapes_agents_write(fields: str) -> None:
    """An honest report must not be rejected for its markdown style.

    Nothing tells the agent which of these shapes the parser wants, and the two
    most natural ones were rejected — so the gate punished the honest report it
    exists to collect.
    """
    _content, diagnostics = parse_and_validate(
        "---\ntype: development_result\nstatus: partial\n---\n"
        "## Summary\n- [SUM-1] S-4 incomplete.\n"
        "## Incomplete Work\n- [S-4] Rename API endpoints: half-applied.\n" + fields,
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []


@pytest.mark.usefixtures("cycle_already_warned")
def test_a_bracketless_incomplete_item_is_told_what_is_wrong() -> None:
    """A bullet without a stable ID is not an item, so say so.

    The parser only builds an item from a bracketed bullet, so a bracket-less
    line becomes prose and the section reads as empty — and the agent was told
    the section was missing while looking straight at it.
    """
    _content, diagnostics = parse_and_validate(
        """---
type: development_result
status: partial
---
## Summary
- [SUM-1] S-4 incomplete.
## Incomplete Work
- Retry path unfinished.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert any("stable-ID" in diagnostic.message for diagnostic in diagnostics)
    assert not any("is required" in diagnostic.message for diagnostic in diagnostics)


@pytest.mark.usefixtures("cycle_already_warned")
def test_unbracketed_incomplete_work_bullets_are_rejected_not_dropped() -> None:
    """Silent omission is what the gate exists to prevent, so it cannot silently omit.

    A bullet without a stable-ID bracket parses as prose, so a report listing
    one bracketed item and three plain bullets validated clean while the three
    were discarded — exactly the disappearance the documentation promises is
    impossible.
    """
    _content, diagnostics = parse_and_validate(
        """---
type: development_result
status: partial
---
## Summary
- [SUM-1] Three of four steps unfinished.
## Incomplete Work
- [S-1] Rename API endpoints: half-applied.
  Reason: Only half the endpoints were renamed.
  Evidence: tests/test_api.py:42 references the old names.
- Retry path unfinished.
- Cache invalidation not started.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert any("stable-ID" in diagnostic.message for diagnostic in diagnostics)


@pytest.mark.usefixtures("cycle_already_warned")
def test_a_completed_result_keeps_the_incomplete_work_it_declares() -> None:
    """Honest remaining work must not be deleted for being on a completed result."""
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Landed S-1; S-2 deferred with a note.
## Files Changed
- [F-1] src/feature.py
## Plan Items Proven
- [S-1] Added the endpoint rename and updated its callers.
  Disposition: completed
  Rationale: Renamed every call site the plan item listed.
## Incomplete Work
- [S-2] Cache invalidation deferred.
  Reason: Out of scope for the deadline; no safe partial exists.
  Evidence: src/cache.py:11 still uses the eager path.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["incomplete_work"] == ["[S-2] Cache invalidation deferred."]


@pytest.mark.usefixtures("cycle_already_warned")
@pytest.mark.parametrize(
    "extra",
    [
        pytest.param("  - Also the docs update was not done.\n", id="nested-under-item"),
        pytest.param("1. The docs update was not done.\n", id="numbered-list"),
        pytest.param("### [X-1] More\n- [X-2] Docs update not done.\n", id="sub-block"),
    ],
)
def test_extra_unfinished_work_cannot_be_smuggled_past_the_gate(extra: str) -> None:
    """Rejecting only column-zero dashes left three ways to be silently dropped.

    Nesting one level, numbering the list, or opening a sub-block all land the
    text somewhere the guard never looked, so it validated clean and the work
    was erased — the exact silent omission the gate exists to prevent.
    """
    _content, diagnostics = parse_and_validate(
        "---\ntype: development_result\nstatus: partial\n---\n"
        "## Summary\n- [SUM-1] Work remains.\n"
        "## Incomplete Work\n"
        "- [S-3] Rename API endpoints: half-applied.\n"
        "  Reason: Only half the endpoints were renamed.\n"
        "  Evidence: tests/test_api.py:42 references the old names.\n" + extra,
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics != []


@pytest.mark.usefixtures("cycle_not_yet_warned")
def test_an_unwarned_completed_result_may_note_deferred_work_plainly() -> None:
    """Outside a warning the two statuses must not be held to different bars.

    A byte-identical section was accepted under `partial` and rejected under
    `completed`, for a rule the format documentation scopes to warned results
    only — leaving the agent no way to reconcile the diagnostic.
    """
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Landed S-1; noted a deferral.
## Files Changed
- [F-1] src/feature.py
## Incomplete Work
- [S-9] Deferred the docs polish to the next cycle.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["incomplete_work"] == ["[S-9] Deferred the docs polish to the next cycle."]
