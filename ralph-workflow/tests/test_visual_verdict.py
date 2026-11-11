from __future__ import annotations

import inspect
from collections.abc import Sequence

import pytest

from ralph.testing import audit_repo_structure
from ralph.visual.capture_cell import CaptureCell
from ralph.visual.capture_request import CaptureRequest
from ralph.visual.capture_set import CaptureSet
from ralph.visual.design_verdict import (
    VERDICT_BLOCKED,
    VERDICT_FAIL,
    VERDICT_PASS,
    DesignVerdict,
)
from ralph.visual.policy_facts import REQUIRED_STATES, Viewport
from ralph.visual.visual_finding import Region, VisualFinding

_DEFAULT_VIEWPORTS: tuple[Viewport, ...] = (
    Viewport(name="narrow", width=375, height=812),
    Viewport(name="wide", width=1440, height=900),
)
_DEFAULT_THEMES: tuple[str, ...] = ("light", "dark")


def _build_request(*, target: str) -> CaptureRequest:
    return CaptureRequest.build(
        target=target,
        viewports=_DEFAULT_VIEWPORTS,
        themes=_DEFAULT_THEMES,
        states=REQUIRED_STATES,
    )


def _build_capture_set(
    *,
    target: str,
    run_id: str,
    handle_prefix: str,
) -> CaptureSet:
    request = _build_request(target=target)
    cells: list[CaptureCell] = []
    for index, cell in enumerate(request.matrix):
        cells.append(
            CaptureCell(
                target=cell.target,
                viewport=cell.viewport,
                theme=cell.theme,
                state=cell.state,
                cell_id=cell.cell_id,
            )
        )
        assert f"{handle_prefix}-{index}".startswith(handle_prefix)
    return CaptureSet(target=target, cells=tuple(cells), run_id=run_id)


def _finding_for(
    capture_id: str,
    *,
    severity: str = "minor",
) -> VisualFinding:
    return VisualFinding(
        capture_id=capture_id,
        region=Region(x=0, y=0, w=10, h=10),
        dimension="alignment",
        severity=severity,
        narrative="Off-grid by 2px at the smallest breakpoint.",
    )


def _verdict(
    *,
    before: CaptureSet,
    after: CaptureSet,
    intent: str = "The header should align its three action buttons in a single row.",
    findings: Sequence[VisualFinding] = (),
    status: str = VERDICT_PASS,
) -> DesignVerdict:
    return DesignVerdict(
        before=before,
        after=after,
        intent=intent,
        status=status,
        findings=tuple(findings),
    )


def test_visual_finding_has_one_public_top_level_class() -> None:
    from ralph.visual import visual_finding

    source = inspect.getsource(visual_finding)
    public_classes, _, _ = audit_repo_structure._scan_structure(source, tuple(source.splitlines()))

    assert public_classes == ("Region",)


def test_verdict_accepts_exactly_three_inputs() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    sample = _finding_for(next(iter(before.cell_ids)))
    verdict = _verdict(
        before=before,
        after=after,
        findings=(sample,),
    )
    assert verdict.before is before
    assert verdict.after is after
    assert verdict.cell_ids == before.cell_ids
    assert verdict.status == VERDICT_PASS


def test_verdict_requires_before_to_be_a_capture_set() -> None:
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    not_a_capture_set: object = "not-a-capture-set"
    with pytest.raises(ValueError, match="DesignVerdict.before must be a CaptureSet"):
        DesignVerdict(
            before=not_a_capture_set,
            after=after,
            intent="x",
            status=VERDICT_PASS,
            findings=(),
        )


def test_verdict_requires_after_to_be_a_capture_set() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    not_a_capture_set: object = "not-a-capture-set"
    with pytest.raises(ValueError, match="DesignVerdict.after must be a CaptureSet"):
        DesignVerdict(
            before=before,
            after=not_a_capture_set,
            intent="x",
            status=VERDICT_PASS,
            findings=(),
        )


def test_verdict_requires_non_empty_intent() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    with pytest.raises(ValueError, match="DesignVerdict.intent must be a non-empty string"):
        _verdict(before=before, after=after, intent="")
    with pytest.raises(ValueError, match="DesignVerdict.intent must be a non-empty string"):
        _verdict(before=before, after=after, intent="   ")


def test_verdict_status_must_match_closed_vocabulary() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    with pytest.raises(ValueError, match="DesignVerdict.status must be one of"):
        DesignVerdict(
            before=before,
            after=after,
            intent="x",
            status="maybe",
            findings=(),
        )


def test_verdict_findings_must_be_a_tuple_of_visual_finding_instances() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    sample = _finding_for(next(iter(before.cell_ids)))
    not_a_tuple: object = [sample]
    with pytest.raises(ValueError, match="DesignVerdict.findings must be a tuple"):
        DesignVerdict(
            before=before,
            after=after,
            intent="x",
            status=VERDICT_PASS,
            findings=not_a_tuple,
        )


def test_verdict_rejects_intent_with_source_smuggle_phrase() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    with pytest.raises(ValueError, match="must not smuggle non-capture artifacts"):
        _verdict(
            before=before,
            after=after,
            intent="Inspect the source code of the rendered header.",
        )


def test_verdict_rejects_intent_with_diff_smuggle_phrase() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    with pytest.raises(ValueError, match="must not smuggle non-capture artifacts"):
        _verdict(
            before=before,
            after=after,
            intent="Compare the diff hunks between the two versions.",
        )


def test_verdict_rejects_intent_with_dom_smuggle_phrase() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    with pytest.raises(ValueError, match="must not smuggle non-capture artifacts"):
        _verdict(
            before=before,
            after=after,
            intent="Walk the dom tree of the rendered header.",
        )


def test_verdict_rejects_intent_with_stylesheet_smuggle_phrase() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    with pytest.raises(ValueError, match="must not smuggle non-capture artifacts"):
        _verdict(
            before=before,
            after=after,
            intent="Compare this against the production stylesheet contents.",
        )


def test_verdict_rejects_target_mismatch() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="dashboard", run_id="run-2", handle_prefix="after")
    with pytest.raises(ValueError, match="target mismatch"):
        _verdict(before=before, after=after)


def test_verdict_rejects_matrix_parity_mismatch() -> None:
    before_request = CaptureRequest.build(
        target="checkout",
        viewports=_DEFAULT_VIEWPORTS,
        themes=_DEFAULT_THEMES,
        states=REQUIRED_STATES,
    )
    after_request = CaptureRequest.build(
        target="checkout",
        viewports=_DEFAULT_VIEWPORTS,
        themes=("light",),
        states=REQUIRED_STATES,
    )
    before = CaptureSet(
        target="checkout",
        cells=before_request.matrix,
        run_id="run-1",
    )
    after = CaptureSet(
        target="checkout",
        cells=after_request.matrix,
        run_id="run-2",
    )
    with pytest.raises(ValueError, match="same capture matrix"):
        _verdict(before=before, after=after)


def test_verdict_rejects_after_missing_required_states() -> None:
    before_request = CaptureRequest.build(
        target="checkout",
        viewports=_DEFAULT_VIEWPORTS,
        themes=_DEFAULT_THEMES,
        states=REQUIRED_STATES,
    )
    cells = [
        CaptureCell.mint(
            target="checkout",
            viewport=viewport,
            theme=theme,
            state=state,
        )
        for viewport in _DEFAULT_VIEWPORTS
        for theme in _DEFAULT_THEMES
        for state in REQUIRED_STATES
        if state != "overflow"
    ]
    before = CaptureSet(
        target="checkout",
        cells=before_request.matrix,
        run_id="run-1",
    )
    after = CaptureSet(target="checkout", cells=tuple(cells), run_id="run-2")
    with pytest.raises(ValueError, match="same capture matrix"):
        _verdict(before=before, after=after)

    overflow_cells = [
        CaptureCell.mint(
            target="checkout",
            viewport=viewport,
            theme=theme,
            state="overflow",
        )
        for viewport in _DEFAULT_VIEWPORTS
        for theme in _DEFAULT_THEMES
    ]
    cells_with_overflow = list(cells) + overflow_cells
    after_complete = CaptureSet(
        target="checkout",
        cells=tuple(cells_with_overflow),
        run_id="run-2",
    )
    verdict = _verdict(before=before, after=after_complete)
    assert verdict.cell_ids == before.cell_ids


def test_verdict_rejects_finding_with_unknown_capture_id() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    ghost = _finding_for("not-in-cell-ids")
    with pytest.raises(ValueError, match="not in the shared capture matrix"):
        _verdict(before=before, after=after, findings=(ghost,))


def test_verdict_pass_with_blocker_finding_is_rejected() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    blocker = _finding_for(next(iter(before.cell_ids)), severity="blocker")
    with pytest.raises(ValueError, match="status='pass' is inconsistent"):
        _verdict(before=before, after=after, findings=(blocker,))


def test_verdict_fail_with_no_blocking_finding_is_rejected() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    minor = _finding_for(next(iter(before.cell_ids)), severity="minor")
    with pytest.raises(ValueError, match="status='fail' requires at least one"):
        _verdict(
            before=before,
            after=after,
            status=VERDICT_FAIL,
            findings=(minor,),
        )


def test_verdict_blocked_status_is_permissive_about_findings() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    minor = _finding_for(next(iter(before.cell_ids)), severity="minor")
    verdict = _verdict(
        before=before,
        after=after,
        status=VERDICT_BLOCKED,
        findings=(minor,),
    )
    assert verdict.status == VERDICT_BLOCKED
    assert verdict.majors() == ()
    assert verdict.blockers() == ()


def test_verdict_accessors_group_findings_by_capture_id() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    cells = sorted(before.cell_ids)
    first = cells[0]
    second = cells[1]
    finding_a = _finding_for(first, severity="minor")
    finding_b = _finding_for(first, severity="major")
    finding_c = _finding_for(second, severity="blocker")
    verdict = _verdict(
        before=before,
        after=after,
        status=VERDICT_FAIL,
        findings=(finding_a, finding_b, finding_c),
    )
    assert verdict.findings_for(first) == (finding_a, finding_b)
    assert verdict.findings_for(second) == (finding_c,)
    assert verdict.findings_for("not-a-cell-id") == ()
    blockers = verdict.blockers()
    assert len(blockers) == 1
    assert blockers[0].severity == "blocker"
    majors = verdict.majors()
    assert len(majors) == 1
    assert majors[0].severity == "major"


def test_verdict_rejects_absolute_finding_without_before_grounding() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    finding = VisualFinding(
        capture_id=next(iter(before.cell_ids)),
        region=Region(x=0, y=0, w=10, h=10),
        dimension="alignment",
        severity="minor",
        narrative="The buttons should be centered on the page.",
    )
    with pytest.raises(ValueError, match="without a before-grounding phrase"):
        _verdict(before=before, after=after, findings=(finding,))


def test_verdict_accepts_absolute_finding_with_before_grounding_phrase() -> None:
    before = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    finding = VisualFinding(
        capture_id=next(iter(before.cell_ids)),
        region=Region(x=0, y=0, w=10, h=10),
        dimension="alignment",
        severity="minor",
        narrative="The buttons should be centered on the page, compared to before they were left-aligned.",
    )
    verdict = _verdict(before=before, after=after, findings=(finding,))
    assert verdict.status == VERDICT_PASS


def test_verdict_equality_is_identity_keyed() -> None:
    before_a = _build_capture_set(target="checkout", run_id="run-1", handle_prefix="before")
    after_a = _build_capture_set(target="checkout", run_id="run-2", handle_prefix="after")
    before_b = _build_capture_set(target="checkout", run_id="run-3", handle_prefix="before")
    after_b = _build_capture_set(target="checkout", run_id="run-4", handle_prefix="after")
    sample = _finding_for(next(iter(before_a.cell_ids)))
    verdict_a = _verdict(before=before_a, after=after_a, findings=(sample,))
    verdict_b = _verdict(before=before_b, after=after_b, findings=(sample,))
    assert verdict_a == verdict_a
    assert verdict_a != verdict_b
