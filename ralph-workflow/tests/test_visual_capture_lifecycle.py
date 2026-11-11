from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ralph.testing import audit_repo_structure
from ralph.visual.capture_cell import CaptureCell
from ralph.visual.capture_lifecycle import (
    MANIFEST_DIR_RELPATH,
    BaselineStorageError,
    CaptureLifecycle,
    DuplicateBaselineError,
    MissingBaselineError,
    compute_matrix_key,
)
from ralph.visual.capture_request import CaptureRequest
from ralph.visual.capture_set import CaptureSet
from ralph.visual.policy_facts import (
    DEFAULT_THEMES,
    REQUIRED_STATES,
    Viewport,
)


def _fake_clock() -> float:
    return 1_700_000_000.0


def _build_capture_set(
    *, target: str, run_id: str, capture_run_id: str | None = None
) -> tuple[CaptureSet, str]:
    viewports = (
        Viewport(name="narrow", width=375, height=812),
        Viewport(name="wide", width=1440, height=900),
    )
    themes = DEFAULT_THEMES
    states: tuple[str, ...] = REQUIRED_STATES
    request = CaptureRequest.build(
        target=target,
        viewports=viewports,
        themes=themes,
        states=states,
    )
    capture_set = CaptureSet(
        target=target,
        cells=request.matrix,
        run_id=capture_run_id or run_id,
    )
    matrix_key = compute_matrix_key(
        viewports=viewports,
        themes=themes,
        states=states,
    )
    return (capture_set, matrix_key)


def test_capture_lifecycle_has_one_public_top_level_class() -> None:
    from ralph.visual import capture_lifecycle

    source = inspect.getsource(capture_lifecycle)
    public_classes, _, _ = audit_repo_structure._scan_structure(source, tuple(source.splitlines()))

    assert public_classes == ("CaptureLifecycle",)


def test_lifecycle_is_run_scoped(tmp_path: Path) -> None:
    target = "checkout-page"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-A")

    lifecycle_a = CaptureLifecycle(
        tmp_path,
        run_id="run-A",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    lifecycle_b = CaptureLifecycle(
        tmp_path,
        run_id="run-B",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )

    lifecycle_a.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )

    assert (
        lifecycle_a.get_retained_before_set(
            target=target,
            matrix_key=matrix_key,
        )
        is not None
    )
    assert (
        lifecycle_b.get_retained_before_set(
            target=target,
            matrix_key=matrix_key,
        )
        is None
    )
    assert (tmp_path / MANIFEST_DIR_RELPATH / "run-A.json").exists()
    assert not (tmp_path / MANIFEST_DIR_RELPATH / "run-B.json").exists()


def test_lifecycle_is_cycle_scoped(tmp_path: Path) -> None:
    target = "settings-page"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")

    cycle_one = CaptureLifecycle(
        tmp_path,
        run_id="run-1",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    cycle_two = CaptureLifecycle(
        tmp_path,
        run_id="run-1",
        cycle_id="cycle-2",
        clock=_fake_clock,
    )

    cycle_one.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )

    assert (
        cycle_one.get_retained_before_set(
            target=target,
            matrix_key=matrix_key,
        )
        is not None
    )
    assert (
        cycle_two.get_retained_before_set(
            target=target,
            matrix_key=matrix_key,
        )
        is None
    )


def test_lifecycle_retains_across_calls_in_same_instance(tmp_path: Path) -> None:
    target = "profile-page"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")

    lifecycle = CaptureLifecycle(
        tmp_path,
        run_id="run-1",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    assert (
        lifecycle.get_retained_before_set(
            target=target,
            matrix_key=matrix_key,
        )
        is None
    )

    lifecycle.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )

    retained = lifecycle.get_retained_before_set(
        target=target,
        matrix_key=matrix_key,
    )
    assert retained is not None
    assert retained.target == target
    assert retained.cell_ids == capture_set.cell_ids
    assert retained.run_id == capture_set.run_id


def test_lifecycle_retains_across_retries(tmp_path: Path) -> None:
    target = "dashboard"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")

    first = CaptureLifecycle(
        tmp_path,
        run_id="run-1",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    first.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )

    retry = CaptureLifecycle(
        tmp_path,
        run_id="run-1",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    retained = retry.get_retained_before_set(
        target=target,
        matrix_key=matrix_key,
    )
    assert retained is not None
    assert retained.target == target
    assert retained.cell_ids == capture_set.cell_ids


def test_lifecycle_persists_to_expected_path(tmp_path: Path) -> None:
    target = "landing"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-persist")

    lifecycle = CaptureLifecycle(
        tmp_path,
        run_id="run-persist",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    lifecycle.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )

    manifest_path = tmp_path / MANIFEST_DIR_RELPATH / "run-persist.json"
    assert manifest_path.exists()
    contents = manifest_path.read_text(encoding="utf-8")
    assert '"schema_version": "1"' in contents
    assert "run-persist" in contents


def test_get_retained_before_set_returns_none_when_absent(tmp_path: Path) -> None:
    lifecycle = CaptureLifecycle(
        tmp_path,
        run_id="run-1",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    result = lifecycle.get_retained_before_set(
        target="never-captured",
        matrix_key="0" * 64,
    )
    assert result is None


def test_require_before_set_raises_when_absent(tmp_path: Path) -> None:
    lifecycle = CaptureLifecycle(
        tmp_path,
        run_id="run-1",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    with pytest.raises(MissingBaselineError) as excinfo:
        lifecycle.require_before_set(
            target="never-captured",
            matrix_key="0" * 64,
        )
    assert excinfo.value.target == "never-captured"
    assert excinfo.value.matrix_key == "0" * 64


def test_require_before_set_returns_baseline_when_present(tmp_path: Path) -> None:
    target = "checkout"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")
    lifecycle = CaptureLifecycle(
        tmp_path,
        run_id="run-1",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    lifecycle.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )
    retained = lifecycle.require_before_set(
        target=target,
        matrix_key=matrix_key,
    )
    assert retained.cell_ids == capture_set.cell_ids


def test_require_before_set_fails_for_mismatched_matrix_key(tmp_path: Path) -> None:
    target = "checkout"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")
    lifecycle = CaptureLifecycle(
        tmp_path,
        run_id="run-1",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    lifecycle.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )
    other_key = "1" * 64
    assert other_key != matrix_key
    with pytest.raises(MissingBaselineError) as excinfo:
        lifecycle.require_before_set(target=target, matrix_key=other_key)
    assert excinfo.value.matrix_key == other_key


def test_capture_before_set_rejects_duplicate(tmp_path: Path) -> None:
    target = "profile"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")
    lifecycle = CaptureLifecycle(
        tmp_path,
        run_id="run-1",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    lifecycle.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )
    with pytest.raises(DuplicateBaselineError) as excinfo:
        lifecycle.capture_before_set(
            target=target,
            capture_set=capture_set,
            matrix_key=matrix_key,
            design_capture_command="bin/capture --target={target}",
        )
    assert excinfo.value.target == target
    assert excinfo.value.matrix_key == matrix_key


def test_capture_before_set_rejects_target_mismatch(tmp_path: Path) -> None:
    capture_set, matrix_key = _build_capture_set(target="real-target", run_id="run-1")
    lifecycle = CaptureLifecycle(
        tmp_path,
        run_id="run-1",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    with pytest.raises(BaselineStorageError):
        lifecycle.capture_before_set(
            target="different-target",
            capture_set=capture_set,
            matrix_key=matrix_key,
            design_capture_command="bin/capture --target={target}",
        )


def test_capture_before_set_rejects_non_capture_set(tmp_path: Path) -> None:
    lifecycle = CaptureLifecycle(
        tmp_path,
        run_id="run-1",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    with pytest.raises(BaselineStorageError):
        lifecycle.capture_before_set(
            target="x",
            capture_set=object(),
            matrix_key="0" * 64,
            design_capture_command="bin/capture --target={target}",
        )


def test_capture_lifecycle_rejects_blank_run_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        CaptureLifecycle(tmp_path, run_id="  ", cycle_id="cycle-1")


def test_capture_lifecycle_rejects_blank_cycle_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        CaptureLifecycle(tmp_path, run_id="run-1", cycle_id="")


def test_compute_matrix_key_is_stable_across_orders() -> None:
    viewports = (
        Viewport(name="narrow", width=375, height=812),
        Viewport(name="wide", width=1440, height=900),
    )
    themes = ("light", "dark")
    states = ("default", "empty", "loading")
    key_a = compute_matrix_key(viewports=viewports, themes=themes, states=states)
    key_b = compute_matrix_key(viewports=viewports, themes=themes, states=states)
    assert key_a == key_b
    assert len(key_a) == 64


def test_compute_matrix_key_changes_when_axes_change() -> None:
    viewports = (Viewport(name="narrow", width=375, height=812),)
    themes = ("light",)
    states = ("default",)
    base = compute_matrix_key(viewports=viewports, themes=themes, states=states)
    other = compute_matrix_key(
        viewports=viewports,
        themes=("dark",),
        states=states,
    )
    assert base != other


def test_retained_capture_set_round_trips_cells(tmp_path: Path) -> None:
    target = "cart"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")
    lifecycle = CaptureLifecycle(
        tmp_path,
        run_id="run-1",
        cycle_id="cycle-1",
        clock=_fake_clock,
    )
    lifecycle.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )

    retained = lifecycle.get_retained_before_set(
        target=target,
        matrix_key=matrix_key,
    )
    assert retained is not None
    assert retained.target == capture_set.target
    assert retained.run_id == capture_set.run_id
    assert retained.cell_ids == capture_set.cell_ids
    for cell in retained.cells:
        assert isinstance(cell, CaptureCell)
        assert cell.cell_id in capture_set.cell_ids
