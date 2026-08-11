"""Black-box tests for :class:`ralph.visual.capture_lifecycle.CaptureLifecycle`.

The lifecycle is the run-scoped source of truth for the pre-change
capture baseline.  The contract this suite pins:

* The lifecycle is run-scoped: two lifecycles for distinct
  ``run_id`` values never share a baseline.
* The lifecycle retains across calls: a ``capture_before_set`` call
  is observable via ``get_retained_before_set`` in the same
  instance.
* The lifecycle retains across retries/continuations: a fresh
  lifecycle instance for the same ``(run_id, cycle_id)`` re-reads
  the same baseline from disk.
* The lifecycle fails closed: when a comparative verdict asks for a
  baseline that was never captured, ``require_before_set`` raises
  :class:`MissingBaselineError` and ``get_retained_before_set``
  returns ``None`` \u2014 the verdict layer cannot accidentally
  fabricate a baseline.
* The pre-change manifest is append-only and immutable within a
  cycle: a second ``capture_before_set`` for the same
  ``(target, matrix_key)`` raises
  :class:`DuplicateBaselineError`.

Tests are pure in-memory (no real subprocess, no real wire ledger,
no ``time.sleep``) and inject a deterministic clock so captured_at
values are stable.  All assertions go through the PUBLIC surface
(``capture_before_set`` / ``get_retained_before_set`` /
``require_before_set``) so the test file stays free of
type-ignore comments per the AGENTS.md type-ignore policy.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import cast

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

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fake_clock() -> float:
    """Return a stable timestamp so captured_at values are deterministic."""
    return 1_700_000_000.0


def _build_capture_set(
    *, target: str, run_id: str, capture_run_id: str | None = None
) -> tuple[CaptureSet, str]:
    """Build a minimal valid CaptureSet and its matrix key for lifecycle tests.

    Two viewports \u00d7 two themes \u00d7 two states (the minimum the
    request layer allows) keeps the cell count low without tripping
    the single-screenshot default rejection in
    :class:`CaptureRequest`.  The matrix key is computed from the
    same axes the request was built with so the caller never has
    to re-derive it (a re-derivation via ``set`` iteration would
    lose the cartesian-product ordering the hash is sensitive to).
    """
    viewports = (
        Viewport(name="narrow", width=375, height=812),
        Viewport(name="wide", width=1440, height=900),
    )
    themes = DEFAULT_THEMES
    states: tuple[str, ...] = REQUIRED_STATES
    request = CaptureRequest.build(
        target=target, viewports=viewports, themes=themes, states=states,
    )
    capture_set = CaptureSet(
        target=target, cells=request.matrix, run_id=capture_run_id or run_id,
    )
    matrix_key = compute_matrix_key(
        viewports=viewports, themes=themes, states=states,
    )
    return (capture_set, matrix_key)


# ---------------------------------------------------------------------------
# Run-scoped
# ---------------------------------------------------------------------------


def test_capture_lifecycle_has_one_public_top_level_class() -> None:
    """The repo-structure audit accepts the lifecycle public API."""
    from ralph.visual import capture_lifecycle

    source = inspect.getsource(capture_lifecycle)
    public_classes, _, _ = audit_repo_structure._scan_structure(
        source, tuple(source.splitlines())
    )

    assert public_classes == ("CaptureLifecycle",)


def test_lifecycle_is_run_scoped(tmp_path: Path) -> None:
    """Two lifecycles for different run_ids do not share baselines."""
    target = "checkout-page"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-A")

    lifecycle_a = CaptureLifecycle(
        tmp_path, run_id="run-A", cycle_id="cycle-1", clock=_fake_clock,
    )
    lifecycle_b = CaptureLifecycle(
        tmp_path, run_id="run-B", cycle_id="cycle-1", clock=_fake_clock,
    )

    lifecycle_a.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )

    # Run-A sees its own baseline; run-B does not.
    assert lifecycle_a.get_retained_before_set(
        target=target, matrix_key=matrix_key,
    ) is not None
    assert lifecycle_b.get_retained_before_set(
        target=target, matrix_key=matrix_key,
    ) is None
    # The manifest files are kept distinct on disk.
    assert (tmp_path / MANIFEST_DIR_RELPATH / "run-A.json").exists()
    assert not (tmp_path / MANIFEST_DIR_RELPATH / "run-B.json").exists()


def test_lifecycle_is_cycle_scoped(tmp_path: Path) -> None:
    """Two cycles for the same run_id do not share baselines."""
    target = "settings-page"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")

    cycle_one = CaptureLifecycle(
        tmp_path, run_id="run-1", cycle_id="cycle-1", clock=_fake_clock,
    )
    cycle_two = CaptureLifecycle(
        tmp_path, run_id="run-1", cycle_id="cycle-2", clock=_fake_clock,
    )

    cycle_one.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )

    assert cycle_one.get_retained_before_set(
        target=target, matrix_key=matrix_key,
    ) is not None
    assert cycle_two.get_retained_before_set(
        target=target, matrix_key=matrix_key,
    ) is None


# ---------------------------------------------------------------------------
# Retains across calls / instances
# ---------------------------------------------------------------------------


def test_lifecycle_retains_across_calls_in_same_instance(tmp_path: Path) -> None:
    """``get_retained_before_set`` returns what ``capture_before_set`` stored."""
    target = "profile-page"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")

    lifecycle = CaptureLifecycle(
        tmp_path, run_id="run-1", cycle_id="cycle-1", clock=_fake_clock,
    )
    assert lifecycle.get_retained_before_set(
        target=target, matrix_key=matrix_key,
    ) is None

    lifecycle.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )

    retained = lifecycle.get_retained_before_set(
        target=target, matrix_key=matrix_key,
    )
    assert retained is not None
    assert retained.target == target
    assert retained.cell_ids == capture_set.cell_ids
    # The retained set's run_id is the ORIGINAL capture run_id, so
    # the verdict layer can prove the before-set came from a real
    # prior run, not from the lifecycle's own run.
    assert retained.run_id == capture_set.run_id


def test_lifecycle_retains_across_retries(tmp_path: Path) -> None:
    """A fresh lifecycle for the same (run_id, cycle_id) re-reads the baseline."""
    target = "dashboard"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")

    first = CaptureLifecycle(
        tmp_path, run_id="run-1", cycle_id="cycle-1", clock=_fake_clock,
    )
    first.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )

    # A retry builds a new lifecycle instance from scratch.  It must
    # observe the baseline the first instance wrote to disk.
    retry = CaptureLifecycle(
        tmp_path, run_id="run-1", cycle_id="cycle-1", clock=_fake_clock,
    )
    retained = retry.get_retained_before_set(
        target=target, matrix_key=matrix_key,
    )
    assert retained is not None
    assert retained.target == target
    assert retained.cell_ids == capture_set.cell_ids


def test_lifecycle_persists_to_expected_path(tmp_path: Path) -> None:
    """The manifest file lands at ``.agent/tmp/visual-baseline/{run_id}.json``."""
    target = "landing"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-persist")

    lifecycle = CaptureLifecycle(
        tmp_path, run_id="run-persist", cycle_id="cycle-1", clock=_fake_clock,
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
    # Schema marker is on disk so a future reader can refuse an
    # unknown schema before mistaking it for the current shape.
    assert "\"schema_version\": \"1\"" in contents
    assert "run-persist" in contents


# ---------------------------------------------------------------------------
# Fail-closed semantics
# ---------------------------------------------------------------------------


def test_get_retained_before_set_returns_none_when_absent(tmp_path: Path) -> None:
    """``get_retained_before_set`` returns ``None`` for an un-captured (target, matrix)."""
    lifecycle = CaptureLifecycle(
        tmp_path, run_id="run-1", cycle_id="cycle-1", clock=_fake_clock,
    )
    result = lifecycle.get_retained_before_set(
        target="never-captured", matrix_key="0" * 64,
    )
    assert result is None


def test_require_before_set_raises_when_absent(tmp_path: Path) -> None:
    """``require_before_set`` raises :class:`MissingBaselineError` when no baseline exists.

    The exception is the fail-closed contract: a comparative verdict
    layer that asks for a missing baseline must be unable to
    proceed.  The exception's ``target`` and ``matrix_key`` let the
    caller route the failure without parsing the message.
    """
    lifecycle = CaptureLifecycle(
        tmp_path, run_id="run-1", cycle_id="cycle-1", clock=_fake_clock,
    )
    with pytest.raises(MissingBaselineError) as excinfo:
        lifecycle.require_before_set(
            target="never-captured", matrix_key="0" * 64,
        )
    assert excinfo.value.target == "never-captured"
    assert excinfo.value.matrix_key == "0" * 64


def test_require_before_set_returns_baseline_when_present(tmp_path: Path) -> None:
    """``require_before_set`` returns the baseline instead of raising when present."""
    target = "checkout"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")
    lifecycle = CaptureLifecycle(
        tmp_path, run_id="run-1", cycle_id="cycle-1", clock=_fake_clock,
    )
    lifecycle.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )
    retained = lifecycle.require_before_set(
        target=target, matrix_key=matrix_key,
    )
    assert retained.cell_ids == capture_set.cell_ids


def test_require_before_set_fails_for_mismatched_matrix_key(tmp_path: Path) -> None:
    """A ``matrix_key`` that does not match the stored baseline is fail-closed."""
    target = "checkout"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")
    lifecycle = CaptureLifecycle(
        tmp_path, run_id="run-1", cycle_id="cycle-1", clock=_fake_clock,
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


# ---------------------------------------------------------------------------
# Append-only invariant
# ---------------------------------------------------------------------------


def test_capture_before_set_rejects_duplicate(tmp_path: Path) -> None:
    """A second ``capture_before_set`` for the same (cycle, target, matrix) raises."""
    target = "profile"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")
    lifecycle = CaptureLifecycle(
        tmp_path, run_id="run-1", cycle_id="cycle-1", clock=_fake_clock,
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


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_capture_before_set_rejects_target_mismatch(tmp_path: Path) -> None:
    """A CaptureSet whose target differs from the requested target is rejected."""
    capture_set, matrix_key = _build_capture_set(target="real-target", run_id="run-1")
    lifecycle = CaptureLifecycle(
        tmp_path, run_id="run-1", cycle_id="cycle-1", clock=_fake_clock,
    )
    with pytest.raises(BaselineStorageError):
        lifecycle.capture_before_set(
            target="different-target",
            capture_set=capture_set,
            matrix_key=matrix_key,
            design_capture_command="bin/capture --target={target}",
        )


def test_capture_before_set_rejects_non_capture_set(tmp_path: Path) -> None:
    """A non-CaptureSet input is rejected at the type-checked boundary.

    ``cast(CaptureSet, object())`` is the typing-safe way to lie to
    mypy about the parameter type so the runtime ``isinstance`` check
    inside :meth:`CaptureLifecycle.capture_before_set` fires without
    us having to add a ``# mypy suppression`` comment in a test file
    (which the project policy forbids).
    """
    lifecycle = CaptureLifecycle(
        tmp_path, run_id="run-1", cycle_id="cycle-1", clock=_fake_clock,
    )
    with pytest.raises(BaselineStorageError):
        lifecycle.capture_before_set(
            target="x",
            capture_set=cast("CaptureSet", object()),
            matrix_key="0" * 64,
            design_capture_command="bin/capture --target={target}",
        )


def test_capture_lifecycle_rejects_blank_run_id(tmp_path: Path) -> None:
    """A blank ``run_id`` is rejected at construction time."""
    with pytest.raises(ValueError):
        CaptureLifecycle(tmp_path, run_id="  ", cycle_id="cycle-1")


def test_capture_lifecycle_rejects_blank_cycle_id(tmp_path: Path) -> None:
    """A blank ``cycle_id`` is rejected at construction time."""
    with pytest.raises(ValueError):
        CaptureLifecycle(tmp_path, run_id="run-1", cycle_id="")


# ---------------------------------------------------------------------------
# Matrix key
# ---------------------------------------------------------------------------


def test_compute_matrix_key_is_stable_across_orders() -> None:
    """A matrix key is invariant under repeated calls with the same axes."""
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
    """A single axis change produces a different matrix key."""
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


# ---------------------------------------------------------------------------
# Retained CaptureSet cell fidelity
# ---------------------------------------------------------------------------


def test_retained_capture_set_round_trips_cells(tmp_path: Path) -> None:
    """A retained CaptureSet has the same cell coverage as the original."""
    target = "cart"
    capture_set, matrix_key = _build_capture_set(target=target, run_id="run-1")
    lifecycle = CaptureLifecycle(
        tmp_path, run_id="run-1", cycle_id="cycle-1", clock=_fake_clock,
    )
    lifecycle.capture_before_set(
        target=target,
        capture_set=capture_set,
        matrix_key=matrix_key,
        design_capture_command="bin/capture --target={target}",
    )

    retained = lifecycle.get_retained_before_set(
        target=target, matrix_key=matrix_key,
    )
    assert retained is not None
    assert retained.target == capture_set.target
    assert retained.run_id == capture_set.run_id
    assert retained.cell_ids == capture_set.cell_ids
    # Each retained cell carries the original cell_id verbatim \u2014
    # the verdict layer uses cell_ids to cross-reference findings
    # against the baseline.
    for cell in retained.cells:
        assert isinstance(cell, CaptureCell)
        assert cell.cell_id in capture_set.cell_ids
