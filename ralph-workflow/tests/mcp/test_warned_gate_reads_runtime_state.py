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
