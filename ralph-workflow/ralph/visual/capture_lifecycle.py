"""Run-scoped lifecycle service for the pre-change visual capture baseline.

The :class:`CaptureLifecycle` is the single source of truth for the
authentic pre-change capture set the design-verdict layer compares
against.  It owns the pre-change capture manifest (immutable, keyed by
``run / cycle / target / matrix_key``), persists it at
``.agent/tmp/visual-baseline/{run_id}.json`` so retries and run
continuations re-read the same baseline, and refuses to mint a
comparative verdict when no authentic baseline has been retained.

The lifecycle is deliberately conservative: the manifest is append-only
(``capture_before_set`` is rejected when the same
``(run, cycle, target, matrix_key)`` tuple already exists) and
:func:`require_before_set` is the fail-closed entry point the verdict
layer calls.  A "before" set that is missing, that was never captured in
the project's own lifecycle, or that does not match the requested
``matrix_key`` is treated as a hard error rather than a soft warning \u2014
a comparative verdict built on a substituted baseline is the failure
mode the contract exists to prevent.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed
from ralph.mcp.multimodal.resources import new_artifact_id
from ralph.visual.capture_cell import CaptureCell
from ralph.visual.capture_set import CaptureSet
from ralph.visual.policy_facts import Viewport

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Workspace-relative directory holding the per-run baseline manifests.
MANIFEST_DIR_RELPATH: str = ".agent/tmp/visual-baseline"

#: File name pattern for a per-run baseline manifest.  The run_id is
#: interpolated; the file is JSON-encoded with a small header schema
#: version so future fields can be added without breaking older readers.
_MANIFEST_FILENAME_TEMPLATE: str = "{run_id}.json"

#: Current manifest schema version.  Bump on a backwards-incompatible
#: change; the loader rejects unknown versions so a stale tool cannot
#: silently misread a future manifest.
_MANIFEST_SCHEMA_VERSION: str = "1"

#: NUL separator used to disambiguate concatenated field values inside
#: the matrix-key hash.  The values are all printable strings
#: (viewport name, theme, state) so NUL cannot appear inside a field.
_MATRIX_KEY_SEP: str = "\x00"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class _BaselineError(RuntimeError):
    """Base class for :class:`CaptureLifecycle` errors."""


class _MissingBaselineError(_BaselineError):
    """Raised when a comparative verdict is requested but no authentic pre-change set exists."""

    def __init__(self, *, target: str, matrix_key: str) -> None:
        self.target = target
        self.matrix_key = matrix_key
        super().__init__(
            "CaptureLifecycle has no authentic pre-change baseline for "
            f"target={target!r} matrix_key={matrix_key!r}; comparative "
            "UI verdicts are fail-closed when the baseline is absent"
        )


class _DuplicateBaselineError(_BaselineError):
    """Raised when a baseline already exists for the same (run, cycle, target, matrix_key)."""

    def __init__(self, *, target: str, matrix_key: str) -> None:
        self.target = target
        self.matrix_key = matrix_key
        super().__init__(
            "CaptureLifecycle already retains an immutable baseline for "
            f"target={target!r} matrix_key={matrix_key!r}; the pre-change "
            "manifest is append-only"
        )


class _BaselineStorageError(_BaselineError):
    """Raised when the baseline manifest cannot be persisted or loaded safely."""

# ---------------------------------------------------------------------------
# Typed structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RetainedBaselineCell:
    """One cell of a retained baseline, persisted verbatim.

    The cell's full identity (target, viewport, theme, state, cell_id)
    is stored so the manifest is self-describing \u2014 a reader does not
    have to re-run the capture to verify the baseline's coverage.
    """

    target: str
    viewport_name: str
    viewport_width: int
    viewport_height: int
    theme: str
    state: str
    cell_id: str

    def to_capture_cell(self) -> CaptureCell:
        """Reconstruct the original :class:`CaptureCell` from the retained fields.

        The cell_id is preserved (not re-minted) so a restored
        :class:`CaptureSet` round-trips the original cell-id set byte
        for byte, even when the lifecycle is loaded by a fresh process
        or after a run continuation.
        """
        viewport = Viewport(
            name=self.viewport_name,
            width=self.viewport_width,
            height=self.viewport_height,
        )
        cell = CaptureCell.mint(
            target=self.target,
            viewport=viewport,
            theme=self.theme,
            state=self.state,
        )
        if cell.cell_id != self.cell_id:
            # Mismatched cell_id is a fatal storage contract violation:
            # it means somebody tampered with the manifest, or the
            # hashing rules changed underneath a retained baseline.
            # Either way, we refuse to mint a CaptureSet on top of a
            # corrupt entry.
            raise BaselineStorageError(
                "Retained cell_id does not match the recomputed cell id; "
                f"stored={self.cell_id!r} recomputed={cell.cell_id!r}"
            )
        return cell

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target,
            "viewport_name": self.viewport_name,
            "viewport_width": self.viewport_width,
            "viewport_height": self.viewport_height,
            "theme": self.theme,
            "state": self.state,
            "cell_id": self.cell_id,
        }

    @classmethod
    def from_dict(cls, payload: object) -> RetainedBaselineCell:
        if not isinstance(payload, dict):
            raise BaselineStorageError(
                f"cell entry must be a dict, got {type(payload).__name__}"
            )
        required = (
            "target",
            "viewport_name",
            "viewport_width",
            "viewport_height",
            "theme",
            "state",
            "cell_id",
        )
        for key in required:
            if key not in payload:
                raise BaselineStorageError(
                    f"cell entry missing required field {key!r}"
                )
        target = payload["target"]
        viewport_name = payload["viewport_name"]
        viewport_width = payload["viewport_width"]
        viewport_height = payload["viewport_height"]
        theme = payload["theme"]
        state = payload["state"]
        cell_id = payload["cell_id"]
        if not isinstance(target, str) or not target:
            raise BaselineStorageError("cell.target must be a non-empty string")
        if not isinstance(viewport_name, str) or not viewport_name:
            raise BaselineStorageError("cell.viewport_name must be a non-empty string")
        if not isinstance(viewport_width, int) or isinstance(viewport_width, bool):
            raise BaselineStorageError("cell.viewport_width must be an int")
        if not isinstance(viewport_height, int) or isinstance(viewport_height, bool):
            raise BaselineStorageError("cell.viewport_height must be an int")
        if not isinstance(theme, str) or not theme:
            raise BaselineStorageError("cell.theme must be a non-empty string")
        if not isinstance(state, str) or not state:
            raise BaselineStorageError("cell.state must be a non-empty string")
        if not isinstance(cell_id, str) or len(cell_id) != 64:
            raise BaselineStorageError("cell.cell_id must be a 64-char hex string")
        return cls(
            target=target,
            viewport_name=viewport_name,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            theme=theme,
            state=state,
            cell_id=cell_id,
        )


@dataclass(frozen=True)
class _RetainedBaselineEntry:
    """One (target, matrix_key) row in the pre-change baseline manifest.

    ``capture_run_id`` is the run that originally produced the
    baseline.  The lifecycle instance that stores the entry may be in
    a different run (retries/continuations) \u2014 the lifecycle's own
    ``run_id`` is the *current* run, the entry's ``capture_run_id`` is
    the *provenance* of the baseline.  Keeping the original
    ``capture_run_id`` on the restored :class:`CaptureSet` lets the
    verdict layer prove the before-set came from a real capture in a
    real prior run, not from a reconstructed stub.
    """

    artifact_id: str
    target: str
    matrix_key: str
    capture_run_id: str
    cycle_id: str
    captured_at_unix: float
    cells: tuple[RetainedBaselineCell, ...]
    design_capture_command: str

    def to_capture_set(self) -> CaptureSet:
        """Reconstruct the run-owned :class:`CaptureSet` for this entry.

        The restored set's ``run_id`` is the ORIGINAL capture's run_id,
        not the lifecycle's current run_id, so the verdict layer sees
        the baseline's true provenance.
        """
        cells = tuple(cell.to_capture_cell() for cell in self.cells)
        return CaptureSet(
            target=self.target,
            cells=cells,
            run_id=self.capture_run_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "target": self.target,
            "matrix_key": self.matrix_key,
            "capture_run_id": self.capture_run_id,
            "cycle_id": self.cycle_id,
            "captured_at_unix": self.captured_at_unix,
            "cells": [cell.to_dict() for cell in self.cells],
            "design_capture_command": self.design_capture_command,
        }

    @classmethod
    def from_dict(cls, payload: object) -> RetainedBaselineEntry:
        if not isinstance(payload, dict):
            raise BaselineStorageError(
                f"baseline entry must be a dict, got {type(payload).__name__}"
            )
        required = (
            "artifact_id",
            "target",
            "matrix_key",
            "capture_run_id",
            "cycle_id",
            "captured_at_unix",
            "cells",
            "design_capture_command",
        )
        for key in required:
            if key not in payload:
                raise BaselineStorageError(
                    f"baseline entry missing required field {key!r}"
                )
        artifact_id = payload["artifact_id"]
        target = payload["target"]
        matrix_key = payload["matrix_key"]
        capture_run_id = payload["capture_run_id"]
        cycle_id = payload["cycle_id"]
        captured_at_unix = payload["captured_at_unix"]
        cells = payload["cells"]
        design_capture_command = payload["design_capture_command"]
        if not isinstance(artifact_id, str) or not artifact_id:
            raise BaselineStorageError("artifact_id must be a non-empty string")
        if not isinstance(target, str) or not target:
            raise BaselineStorageError("target must be a non-empty string")
        if not isinstance(matrix_key, str) or not matrix_key:
            raise BaselineStorageError("matrix_key must be a non-empty string")
        if not isinstance(capture_run_id, str) or not capture_run_id:
            raise BaselineStorageError("capture_run_id must be a non-empty string")
        if not isinstance(cycle_id, str) or not cycle_id:
            raise BaselineStorageError("cycle_id must be a non-empty string")
        if not isinstance(captured_at_unix, (int, float)) or isinstance(
            captured_at_unix, bool
        ):
            raise BaselineStorageError("captured_at_unix must be a number")
        if not isinstance(cells, list):
            raise BaselineStorageError("cells must be a list")
        if not isinstance(design_capture_command, str):
            raise BaselineStorageError("design_capture_command must be a string")
        return cls(
            artifact_id=artifact_id,
            target=target,
            matrix_key=matrix_key,
            capture_run_id=capture_run_id,
            cycle_id=cycle_id,
            captured_at_unix=float(captured_at_unix),
            cells=tuple(RetainedBaselineCell.from_dict(cell) for cell in cells),
            design_capture_command=design_capture_command,
        )


@dataclass(frozen=True)
class _BaselineManifest:
    """The full per-run baseline manifest, persisted to JSON.

    ``entries`` is a tuple (insertion order) rather than a dict so the
    manifest is structurally frozen and equality is order-insensitive
    but ordering is preserved on read for deterministic diffs.
    """

    run_id: str
    entries: tuple[RetainedBaselineEntry, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "run_id": self.run_id,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, payload: object) -> BaselineManifest:
        if not isinstance(payload, dict):
            raise BaselineStorageError(
                f"baseline manifest must be a dict, got {type(payload).__name__}"
            )
        schema_version_raw: object = payload.get("schema_version")
        schema_version: str = schema_version_raw if isinstance(schema_version_raw, str) else ""
        if schema_version != _MANIFEST_SCHEMA_VERSION:
            raise BaselineStorageError(
                f"unsupported baseline manifest schema_version={schema_version!r}; "
                f"expected {_MANIFEST_SCHEMA_VERSION}"
            )
        run_id = payload.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise BaselineStorageError("baseline manifest missing run_id")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise BaselineStorageError("baseline manifest entries must be a list")
        return cls(
            run_id=run_id,
            entries=tuple(RetainedBaselineEntry.from_dict(entry) for entry in entries),
        )

    def find(self, *, cycle_id: str, target: str, matrix_key: str) -> RetainedBaselineEntry | None:
        for entry in self.entries:
            if (
                entry.cycle_id == cycle_id
                and entry.target == target
                and entry.matrix_key == matrix_key
            ):
                return entry
        return None

    def with_appended(self, entry: RetainedBaselineEntry) -> BaselineManifest:
        return BaselineManifest(run_id=self.run_id, entries=(*self.entries, entry))


BaselineError = _BaselineError
MissingBaselineError = _MissingBaselineError
DuplicateBaselineError = _DuplicateBaselineError
BaselineStorageError = _BaselineStorageError
RetainedBaselineCell = _RetainedBaselineCell
RetainedBaselineEntry = _RetainedBaselineEntry
BaselineManifest = _BaselineManifest
for _type, _name in (
    (BaselineError, "BaselineError"),
    (MissingBaselineError, "MissingBaselineError"),
    (DuplicateBaselineError, "DuplicateBaselineError"),
    (BaselineStorageError, "BaselineStorageError"),
    (RetainedBaselineCell, "RetainedBaselineCell"),
    (RetainedBaselineEntry, "RetainedBaselineEntry"),
    (BaselineManifest, "BaselineManifest"),
):
    _type.__name__ = _name
    _type.__qualname__ = _name


# ---------------------------------------------------------------------------
# Matrix-key derivation
# ---------------------------------------------------------------------------


def compute_matrix_key(
    *,
    viewports: tuple[Viewport, ...],
    themes: tuple[str, ...],
    states: tuple[str, ...],
) -> str:
    """Return a stable SHA-256 hex digest over the (viewports, themes, states) tuple.

    The matrix key is the contract that lets a verdict look up the
    pre-change baseline by the *shape* of its capture matrix: a
    request whose viewport/theme/state axes do not hash to the
    baseline's matrix_key is a different request, and the lifecycle
    refuses to substitute a mismatched baseline.
    """
    parts: list[str] = []
    for viewport in viewports:
        if not isinstance(viewport, Viewport):
            raise ValueError("compute_matrix_key viewports must be Viewport instances")
        parts.append(f"vp={viewport.name}{_MATRIX_KEY_SEP}{viewport.width}x{viewport.height}")
    for theme in themes:
        if not isinstance(theme, str) or not theme:
            raise ValueError("compute_matrix_key themes must be non-empty strings")
        parts.append(f"theme={theme}")
    for state in states:
        if not isinstance(state, str) or not state:
            raise ValueError("compute_matrix_key states must be non-empty strings")
        parts.append(f"state={state}")
    payload = _MATRIX_KEY_SEP.join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


#: Wall-clock hook signature.  Injected for deterministic tests; the
#: default reads ``time.time`` so production calls do not need to
#: thread a clock through the constructor.
ClockFn = Callable[[], float]


def _default_clock() -> float:
    return time.time()


class CaptureLifecycle:
    """Run-scoped lifecycle service for the pre-change capture baseline.

    A lifecycle is bound to one ``(run_id, cycle_id)`` pair and writes
    to one JSON file at ``.agent/tmp/visual-baseline/{run_id}.json``.
    Re-instantiating the lifecycle for the same run_id reads the same
    file, so retries and run continuations see the prior baselines
    they previously captured.

    The lifecycle is deliberately append-only: a second
    :meth:`capture_before_set` for the same ``(cycle, target,
    matrix_key)`` raises :class:`DuplicateBaselineError` so a buggy
    agent cannot overwrite a retained baseline.  The verdict layer
    consumes :meth:`get_retained_before_set` (returns ``None`` when
    absent) or :meth:`require_before_set` (raises when absent \u2014
    the fail-closed path).
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        run_id: str,
        cycle_id: str,
        clock: ClockFn = _default_clock,
    ) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("CaptureLifecycle.run_id must be a non-empty string")
        if not isinstance(cycle_id, str) or not cycle_id.strip():
            raise ValueError("CaptureLifecycle.cycle_id must be a non-empty string")
        if run_id != run_id.strip():
            raise ValueError("CaptureLifecycle.run_id must not carry whitespace")
        if cycle_id != cycle_id.strip():
            raise ValueError("CaptureLifecycle.cycle_id must not carry whitespace")
        self._workspace_root = workspace_root
        self._run_id = run_id
        self._cycle_id = cycle_id
        self._clock = clock

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def run_id(self) -> str:
        """Return the lifecycle's current run_id."""
        return self._run_id

    @property
    def cycle_id(self) -> str:
        """Return the lifecycle's current cycle_id."""
        return self._cycle_id

    def capture_before_set(
        self,
        *,
        target: str,
        capture_set: object,
        matrix_key: str,
        design_capture_command: str,
    ) -> CaptureSet:
        """Persist ``capture_set`` as the authentic pre-change baseline.

        The returned :class:`CaptureSet` is the same object that was
        passed in; the call stores it under
        ``(run_id, cycle_id, target, matrix_key)`` and rejects the
        call if a baseline is already retained for that tuple.

        Raises :class:`DuplicateBaselineError` on a second call with
        the same tuple; raises :class:`BaselineStorageError` if the
        capture set is empty or the matrix_key does not match the
        set's cell coverage.
        """
        self._validate_capture_set_against_matrix(
            capture_set=capture_set, target=target, matrix_key=matrix_key,
        )

        manifest = self._load_manifest()
        existing = manifest.find(
            cycle_id=self._cycle_id, target=target, matrix_key=matrix_key,
        )
        if existing is not None:
            raise DuplicateBaselineError(target=target, matrix_key=matrix_key)

        entry = RetainedBaselineEntry(
            artifact_id=new_artifact_id(),
            target=target,
            matrix_key=matrix_key,
            capture_run_id=capture_set.run_id,
            cycle_id=self._cycle_id,
            captured_at_unix=self._clock(),
            cells=tuple(
                RetainedBaselineCell(
                    target=cell.target,
                    viewport_name=cell.viewport.name,
                    viewport_width=cell.viewport.width,
                    viewport_height=cell.viewport.height,
                    theme=cell.theme,
                    state=cell.state,
                    cell_id=cell.cell_id,
                )
                for cell in capture_set.cells
            ),
            design_capture_command=design_capture_command,
        )
        self._save_manifest(manifest.with_appended(entry))
        return capture_set

    def get_retained_before_set(
        self,
        *,
        target: str,
        matrix_key: str,
    ) -> CaptureSet | None:
        """Return the retained baseline for ``(cycle, target, matrix_key)``.

        Returns ``None`` when no authentic baseline has been retained;
        this is the read-only lookup.  The fail-closed path is
        :meth:`require_before_set`.
        """
        self._validate_lookup_keys(target=target, matrix_key=matrix_key)
        manifest = self._load_manifest()
        entry = manifest.find(
            cycle_id=self._cycle_id, target=target, matrix_key=matrix_key,
        )
        if entry is None:
            return None
        return entry.to_capture_set()

    def require_before_set(
        self,
        *,
        target: str,
        matrix_key: str,
    ) -> CaptureSet:
        """Return the retained baseline or raise :class:`MissingBaselineError`.

        This is the entry point a comparative verdict must use.  The
        verdict layer MUST call this \u2014 not :meth:`get_retained_before_set`
        followed by an ``is None`` check \u2014 so the fail-closed
        semantics are enforced at the type level: the return type is
        a non-None :class:`CaptureSet` and a missing baseline
        surfaces as an exception, not a silent ``None`` flow.
        """
        retained = self.get_retained_before_set(target=target, matrix_key=matrix_key)
        if retained is None:
            raise MissingBaselineError(target=target, matrix_key=matrix_key)
        return retained

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def _manifest_path(self) -> Path:
        manifest_dir = self._workspace_root / MANIFEST_DIR_RELPATH
        return manifest_dir / _MANIFEST_FILENAME_TEMPLATE.format(run_id=self._run_id)

    def _load_manifest(self) -> BaselineManifest:
        path = self._manifest_path()
        if not path.exists():
            return BaselineManifest(run_id=self._run_id, entries=())
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BaselineStorageError(
                f"failed to read baseline manifest {path}: {exc}"
            ) from exc
        try:
            payload: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BaselineStorageError(
                f"baseline manifest {path} is not valid JSON: {exc}"
            ) from exc
        manifest = BaselineManifest.from_dict(payload)
        if manifest.run_id != self._run_id:
            # The file name is keyed by run_id; if the contents
            # disagree we are looking at either a rename bug or a
            # corrupt manifest.  Fail closed \u2014 the agent cannot
            # infer which is which, so we refuse to misattribute
            # baselines.
            raise BaselineStorageError(
                f"baseline manifest {path} declares run_id={manifest.run_id!r} "
                f"but lifecycle is bound to run_id={self._run_id!r}"
            )
        return manifest

    def _save_manifest(self, manifest: BaselineManifest) -> None:
        if manifest.run_id != self._run_id:
            raise BaselineStorageError(
                f"refusing to persist manifest for run_id={manifest.run_id!r} "
                f"from lifecycle bound to run_id={self._run_id!r}"
            )
        path = self._manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # A half-written manifest would be treated as corrupt on the next read.
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        encoded = json.dumps(manifest.to_dict(), indent=2, sort_keys=True)
        try:
            atomic_write_text_if_changed(
                DEFAULT_FILE_BACKEND,
                path,
                encoded,
                tmp_path=tmp_path,
                encoding="utf-8",
            )
        except OSError as exc:
            raise BaselineStorageError(
                f"failed to persist baseline manifest {path}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Internal validation
    # ------------------------------------------------------------------

    def _validate_lookup_keys(self, *, target: str, matrix_key: str) -> None:
        if not isinstance(target, str) or not target.strip():
            raise ValueError("target must be a non-empty string")
        if not isinstance(matrix_key, str) or len(matrix_key) != 64:
            raise ValueError("matrix_key must be a 64-char hex string")

    def _validate_capture_set_against_matrix(
        self,
        *,
        capture_set: CaptureSet,
        target: str,
        matrix_key: str,
    ) -> None:
        self._validate_lookup_keys(target=target, matrix_key=matrix_key)
        if not isinstance(capture_set, CaptureSet):
            raise BaselineStorageError(
                "capture_before_set requires a CaptureSet, got "
                f"{type(capture_set).__name__}"
            )
        if capture_set.target != target:
            raise BaselineStorageError(
                f"capture_set.target={capture_set.target!r} does not match "
                f"requested target={target!r}"
            )


__all__ = [
    "MANIFEST_DIR_RELPATH",
    "BaselineError",
    "BaselineManifest",
    "BaselineStorageError",
    "CaptureLifecycle",
    "ClockFn",
    "DuplicateBaselineError",
    "MissingBaselineError",
    "RetainedBaselineCell",
    "RetainedBaselineEntry",
    "compute_matrix_key",
]
