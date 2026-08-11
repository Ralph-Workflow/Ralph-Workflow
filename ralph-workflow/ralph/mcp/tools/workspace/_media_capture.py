"""Bounded media capture handler: per-cell renderer invocation with HMAC ledger persistence.

The ``handle_media_capture`` handler is the MCP tool entry point that
captures a full visual matrix (viewports \u00d7 themes \u00d7 states) for one
target.  For every cell in the matrix it:

1. Substitutes the target into the policy-declared
   ``design_capture_command`` (or appends it as trailing argv when no
   ``{target}`` placeholder is present),
2. Invokes the renderer through the bounded ``ralph.executor.process.run_process``
   seam with a hard 30-second per-cell timeout,
3. Reads the renderer-written PNG, parses its IHDR for width/height,
   computes its SHA-256, and validates the resolved path stays under
   the workspace root (an out-of-tree write is a fail-closed error),
4. Mints a fresh ``ralph://media/{artifact_id}`` handle,
5. Appends one HMAC-chained wire-ledger record per cell so the
   capture is replayable as ``Provenance.WIRE`` evidence.

The handler fails the WHOLE request when any declared cell fails \u2014
a partial capture set cannot seed a comparative verdict, so the
``handle_media_capture`` contract is "all or nothing": either every
cell produced a valid PNG and every ledger record was appended, or
the request raises :class:`MediaCaptureError` and the caller is
expected to retry from a clean state.

The executor is an injected seam (``executor=``).  Production code
defaults to ``ralph.executor.process.run_process``; tests inject a
fake that records the call and writes PNGs to the expected output
paths under ``tmp_path``.  The wire ledger and process seams are the
two contract surfaces the handler must not bypass, so they are
constructed once on the handler call rather than carried in module
state.
"""

from __future__ import annotations

import hashlib
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ralph.executor._process_result import ProcessResult
from ralph.executor._process_run_options import ProcessRunOptions
from ralph.executor.process import run_process
from ralph.mcp.multimodal.resources import (
    build_media_uri,
    new_artifact_id,
)
from ralph.mcp.server._wire_ledger import append_wire_record
from ralph.visual.capture_request import CaptureRequest

if TYPE_CHECKING:
    from ralph.visual.capture_cell import CaptureCell

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Hard per-cell renderer timeout.  Fail-closed: a renderer that
#: takes longer than 30s to produce a single PNG is treated as a
#: process fault, not a transient slowness.
DEFAULT_PER_CELL_TIMEOUT_SECONDS: float = 30.0

#: Default workspace-relative directory for cell PNGs.
DEFAULT_OUTPUT_DIR_RELPATH: str = ".agent/tmp/visual-captures"

#: PNG magic signature (8 bytes).  Used to reject any output that
#: the renderer wrote but that is not actually a PNG.
_PNG_SIGNATURE: bytes = b"\x89PNG\r\n\x1a\n"

#: PNG IHDR is at byte offset 12; the width/height are 4-byte
#: big-endian integers at offsets 16 and 20.
_PNG_HEADER_SIZE: int = 24

#: Placeholder inside the policy-declared ``design_capture_command``
#: that the handler substitutes with the resolved target string.
_TARGET_PLACEHOLDER: str = "{target}"

#: Environment variables exported to the renderer for every cell.
#: ``RALPH_CAPTURE_OUTPUT`` is the contract for where the renderer
#: MUST write the PNG; the other env vars describe the cell's
#: identity so the renderer can pick a viewport, theme, and state
#: without re-deriving them.
_CAPTURE_OUTPUT_ENV: str = "RALPH_CAPTURE_OUTPUT"
_CAPTURE_TARGET_ENV: str = "RALPH_CAPTURE_TARGET"
_CAPTURE_VIEWPORT_ENV: str = "RALPH_CAPTURE_VIEWPORT"
_CAPTURE_VIEWPORT_WIDTH_ENV: str = "RALPH_CAPTURE_VIEWPORT_WIDTH"
_CAPTURE_VIEWPORT_HEIGHT_ENV: str = "RALPH_CAPTURE_VIEWPORT_HEIGHT"
_CAPTURE_THEME_ENV: str = "RALPH_CAPTURE_THEME"
_CAPTURE_STATE_ENV: str = "RALPH_CAPTURE_STATE"
_CAPTURE_CELL_ID_ENV: str = "RALPH_CAPTURE_CELL_ID"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MediaCaptureError(RuntimeError):
    """Raised when any cell in the capture matrix fails.

    The whole-request fails closed on the first failing cell; the
    exception carries ``target`` and ``cell_id`` so the caller can
    route the failure to a verifier without inspecting the message
    string.
    """

    def __init__(
        self,
        *,
        target: str,
        cell_id: str,
        reason: str,
        returncode: int | None = None,
    ) -> None:
        self.target = target
        self.cell_id = cell_id
        self.reason = reason
        self.returncode = returncode
        super().__init__(reason)


# ---------------------------------------------------------------------------
# Executor seam
# ---------------------------------------------------------------------------


class _ExecutorLike(Protocol):
    """Callable shape for the bounded process executor.

    Matches the real ``ralph.executor.process.run_process`` signature
    closely enough that production code can pass it directly and
    tests can inject a fake with the same shape.
    """

    def __call__(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        options: ProcessRunOptions | None = None,
    ) -> object: ...


# ---------------------------------------------------------------------------
# Typed result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MediaCaptureCellResult:
    """One captured cell's typed outcome."""

    cell: CaptureCell
    artifact_id: str
    uri: str
    sha256: str
    size_bytes: int
    width: int | None
    height: int | None
    output_path: str  # workspace-relative


_MediaCaptureCellResult.__name__ = "MediaCaptureCellResult"
_MediaCaptureCellResult.__qualname__ = "MediaCaptureCellResult"
MediaCaptureCellResult = _MediaCaptureCellResult


@dataclass(frozen=True)
class _MediaCaptureResult:
    """The full typed outcome of a single ``handle_media_capture`` call."""

    target: str
    matrix_key: str
    cells: tuple[_MediaCaptureCellResult, ...]


_MediaCaptureResult.__name__ = "MediaCaptureResult"
_MediaCaptureResult.__qualname__ = "MediaCaptureResult"
MediaCaptureResult = _MediaCaptureResult


# ---------------------------------------------------------------------------
# PNG inspection
# ---------------------------------------------------------------------------


def _png_dimensions(raw_bytes: bytes) -> tuple[int | None, int | None]:
    """Return the (width, height) of a PNG payload, or (None, None).

    Inspects only the IHDR chunk (the renderer MUST write a real PNG
    with a valid IHDR).  Returns ``(None, None)`` for any payload
    shorter than 24 bytes, missing the PNG signature, missing the
    IHDR chunk marker, or whose width/height fail ``int.from_bytes``.
    """
    if len(raw_bytes) < _PNG_HEADER_SIZE:
        return (None, None)
    if raw_bytes[:8] != _PNG_SIGNATURE:
        return (None, None)
    if raw_bytes[12:16] != b"IHDR":
        return (None, None)
    try:
        width = int.from_bytes(raw_bytes[16:20], byteorder="big", signed=False)
        height = int.from_bytes(raw_bytes[20:24], byteorder="big", signed=False)
    except ValueError:
        return (None, None)
    if width <= 0 or height <= 0:
        return (None, None)
    return (width, height)


# ---------------------------------------------------------------------------
# Command resolution
# ---------------------------------------------------------------------------


def _resolve_command(
    *, design_capture_command: str, target: str
) -> tuple[str, tuple[str, ...]]:
    """Resolve ``design_capture_command`` into a (command, args) pair.

    If the command string contains ``{target}``, the placeholder is
    substituted in place.  Otherwise the target is appended as a
    single trailing argv element.  The command is split with
    ``shlex.split`` so quoted arguments round-trip, but a malformed
    command (e.g. unterminated quotes) is rejected as a contract
    violation rather than passing through to the executor.
    """
    if not isinstance(design_capture_command, str) or not design_capture_command.strip():
        raise MediaCaptureError(
            target=target, cell_id="<command>",
            reason="design_capture_command must be a non-empty string",
        )
    if not isinstance(target, str) or not target.strip():
        raise MediaCaptureError(
            target="<unresolved>", cell_id="<command>",
            reason="capture target must be a non-empty string",
        )
    if _TARGET_PLACEHOLDER in design_capture_command:
        resolved = design_capture_command.replace(_TARGET_PLACEHOLDER, target)
        try:
            tokens = shlex.split(resolved)
        except ValueError as exc:
            raise MediaCaptureError(
                target=target, cell_id="<command>",
                reason=f"design_capture_command is not shell-parseable: {exc}",
            ) from exc
        if not tokens:
            raise MediaCaptureError(
                target=target, cell_id="<command>",
                reason="design_capture_command resolved to zero tokens",
            )
        return (tokens[0], tuple(tokens[1:]))
    try:
        tokens = shlex.split(design_capture_command)
    except ValueError as exc:
        raise MediaCaptureError(
            target=target, cell_id="<command>",
            reason=f"design_capture_command is not shell-parseable: {exc}",
        ) from exc
    if not tokens:
        raise MediaCaptureError(
            target=target, cell_id="<command>",
            reason="design_capture_command resolved to zero tokens",
        )
    return (tokens[0], (*tuple(tokens[1:]), target))


def _cell_output_path(*, output_dir_abs: Path, cell_id: str) -> Path:
    """Return the absolute PNG output path the renderer MUST write to."""
    return output_dir_abs / f"{cell_id}.png"


def _relative_posix(path: Path, *, root: Path) -> str:
    """Return ``path`` as a POSIX string relative to ``root``."""
    return path.relative_to(root).as_posix()


# ---------------------------------------------------------------------------
# Wire-ledger record
# ---------------------------------------------------------------------------


def _build_ledger_params(
    *,
    cell: CaptureCell,
    artifact_id: str,
    uri: str,
    sha256: str,
    size_bytes: int,
    width: int | None,
    height: int | None,
    output_path: str,
    matrix_key: str,
) -> dict[str, object]:
    """Build the params payload appended to the HMAC-chained wire ledger.

    Every cell becomes one ledger row, so the params payload is the
    authoritative replay handle for a single capture.  The shape is
    JSON-serialisable and the keys are stable so a replay tool can
    diff two ``ralph://media/{artifact_id}`` calls row-by-row.
    """
    return {
        "cell_id": cell.cell_id,
        "target": cell.target,
        "viewport": cell.viewport.name,
        "viewport_width": cell.viewport.width,
        "viewport_height": cell.viewport.height,
        "theme": cell.theme,
        "state": cell.state,
        "matrix_key": matrix_key,
        "artifact_id": artifact_id,
        "uri": uri,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "width": width,
        "height": height,
        "output_path": output_path,
    }


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_media_capture(
    workspace_root: Path,
    *,
    run_id: str,
    capture_request: CaptureRequest,
    design_capture_command: str,
    output_dir_relpath: str = DEFAULT_OUTPUT_DIR_RELPATH,
    secret: str | None = None,
    executor: _ExecutorLike = run_process,
    per_cell_timeout_seconds: float = DEFAULT_PER_CELL_TIMEOUT_SECONDS,
) -> MediaCaptureResult:
    """Capture every cell in ``capture_request.matrix`` for the resolved target.

    Returns a :class:`MediaCaptureResult` whose ``cells`` tuple mirrors
    the matrix's cell order, each carrying the minted
    ``ralph://media/{artifact_id}`` handle, the PNG's geometry and
    SHA-256, and the workspace-relative output path.  Raises
    :class:`MediaCaptureError` on the first cell that fails for any
    reason (renderer exit, missing/empty/non-PNG output, output
    outside the workspace root, IHDR parse failure); the exception
    carries the failing ``cell_id`` so the caller can route the
    failure.

    The function is fail-closed: a single bad cell aborts the whole
    request and no partial result is returned.  The wire ledger
    appends are sequenced after the PNG is validated, so a failed
    cell never produces a ledger row \u2014 the ledger can only ever
    contain fully-validated cells.
    """
    if not isinstance(workspace_root, Path):
        raise TypeError("workspace_root must be a pathlib.Path")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    if not isinstance(capture_request, CaptureRequest):
        raise TypeError("capture_request must be a CaptureRequest")
    if not isinstance(design_capture_command, str) or not design_capture_command.strip():
        raise ValueError("design_capture_command must be a non-empty string")
    if per_cell_timeout_seconds <= 0:
        raise ValueError("per_cell_timeout_seconds must be positive")

    workspace_root = workspace_root.resolve()
    output_dir_abs = (workspace_root / output_dir_relpath).resolve()
    if not output_dir_abs.is_relative_to(workspace_root):
        # Defense in depth: a misconfigured caller cannot make the
        # handler write outside the workspace root, even if the
        # supplied relpath is a traversal sequence.
        raise ValueError(
            f"output_dir_relpath={output_dir_relpath!r} resolves outside "
            f"the workspace root"
        )
    output_dir_abs.mkdir(parents=True, exist_ok=True)

    target = capture_request.target
    matrix_key = _matrix_key_for(capture_request)
    results: list[_MediaCaptureCellResult] = []
    # Pending cells are buffered so a single failure aborts the WHOLE
    # request without leaving partial ledger rows.  The
    # all-or-nothing contract requires that the wire ledger only ever
    # see fully-validated cells; committing per-cell would let a
    # mid-matrix failure leave a partial set of HMAC-chained records
    # that the verdict layer cannot distinguish from a real capture.
    pending_ledger: list[dict[str, object]] = []

    for cell in capture_request.matrix:
        cell_result, ledger_params = _capture_one_cell(
            workspace_root=workspace_root,
            output_dir_abs=output_dir_abs,
            target=target,
            cell=cell,
            matrix_key=matrix_key,
            design_capture_command=design_capture_command,
            executor=executor,
            per_cell_timeout_seconds=per_cell_timeout_seconds,
        )
        results.append(cell_result)
        pending_ledger.append(ledger_params)

    # Every cell validated.  Commit the wire-ledger rows in matrix
    # order so the chain reflects the call order.  An unsigned server
    # (secret is None) silently writes nothing; the cells are still
    # minted in the result, but the chain stays empty.
    for params in pending_ledger:
        append_wire_record(
            workspace_root,
            method="tools/call",
            tool_name="media_capture",
            params=params,
            run_id=run_id,
            secret=secret,
        )

    return MediaCaptureResult(
        target=target,
        matrix_key=matrix_key,
        cells=tuple(results),
    )


def _matrix_key_for(capture_request: CaptureRequest) -> str:
    """Compute the matrix key for a capture request from its declared axes."""
    # Local import keeps the public module surface narrow and avoids
    # a circular dependency at import time; ``compute_matrix_key`` is
    # in :mod:`ralph.visual.capture_lifecycle`.
    from ralph.visual.capture_lifecycle import compute_matrix_key

    return compute_matrix_key(
        viewports=capture_request.viewports,
        themes=capture_request.themes,
        states=capture_request.states,
    )


def _capture_one_cell(
    *,
    workspace_root: Path,
    output_dir_abs: Path,
    target: str,
    cell: CaptureCell,
    matrix_key: str,
    design_capture_command: str,
    executor: _ExecutorLike,
    per_cell_timeout_seconds: float,
) -> tuple[_MediaCaptureCellResult, dict[str, object]]:
    """Run, validate, and mint one cell; return the result and the pending ledger params.

    The wire-ledger append is NOT performed here \u2014 it is deferred
    to :func:`handle_media_capture` so the all-or-nothing contract
    holds.  Returns the typed :class:`MediaCaptureCellResult` and the
    ``params`` payload that will be appended to the HMAC-chained
    ledger once every cell has validated.
    """
    cell_output_abs = _cell_output_path(output_dir_abs=output_dir_abs, cell_id=cell.cell_id)
    # The handler owns the contract that the renderer writes to a
    # path under the workspace root.  Validate the path up front so
    # a symlink inside the output dir cannot escape the boundary
    # after the renderer returns.
    if not cell_output_abs.is_relative_to(workspace_root):
        raise MediaCaptureError(
            target=target, cell_id=cell.cell_id,
            reason=(
                f"cell output path {cell_output_abs} escapes the workspace root"
            ),
        )

    env = _build_cell_env(
        target=target,
        cell=cell,
        cell_output_abs=cell_output_abs,
    )
    command, args = _resolve_command(
        design_capture_command=design_capture_command, target=target,
    )
    options = ProcessRunOptions(
        cwd=str(workspace_root),
        env=env,
        timeout=per_cell_timeout_seconds,
        capture_output=True,
        label=f"media-capture:{cell.cell_id}",
    )

    try:
        result = executor(command, args, options=options)
    except Exception as exc:
        raise MediaCaptureError(
            target=target, cell_id=cell.cell_id,
            reason=f"renderer invocation raised: {exc}",
        ) from exc

    if not isinstance(result, ProcessResult):
        raise MediaCaptureError(
            target=target, cell_id=cell.cell_id,
            reason=(
                f"executor returned {type(result).__name__}; expected ProcessResult"
            ),
        )
    if not result.succeeded:
        raise MediaCaptureError(
            target=target, cell_id=cell.cell_id,
            reason=(
                f"renderer exited with returncode={result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip() or '<no output>'}"
            ),
            returncode=result.returncode,
        )

    # Renderer succeeded.  Validate the PNG it wrote and build the
    # pending ledger row \u2014 the actual append is deferred to the
    # outer handler so a later failure can still cancel the
    # whole-request commit.
    return _finalize_cell(
        workspace_root=workspace_root,
        target=target,
        cell=cell,
        matrix_key=matrix_key,
        cell_output_abs=cell_output_abs,
    )


def _build_cell_env(
    *,
    target: str,
    cell: CaptureCell,
    cell_output_abs: Path,
) -> dict[str, str]:
    """Build the renderer environment for one cell."""
    return {
        _CAPTURE_OUTPUT_ENV: str(cell_output_abs),
        _CAPTURE_TARGET_ENV: target,
        _CAPTURE_VIEWPORT_ENV: cell.viewport.name,
        _CAPTURE_VIEWPORT_WIDTH_ENV: str(cell.viewport.width),
        _CAPTURE_VIEWPORT_HEIGHT_ENV: str(cell.viewport.height),
        _CAPTURE_THEME_ENV: cell.theme,
        _CAPTURE_STATE_ENV: cell.state,
        _CAPTURE_CELL_ID_ENV: cell.cell_id,
    }


def _finalize_cell(
    *,
    workspace_root: Path,
    target: str,
    cell: CaptureCell,
    matrix_key: str,
    cell_output_abs: Path,
) -> tuple[_MediaCaptureCellResult, dict[str, object]]:
    """Read, validate, and mint one already-rendered cell's PNG.

    Returns the typed :class:`MediaCaptureCellResult` and the
    params payload the outer handler will append to the wire
    ledger.  No file I/O outside the cell's PNG is performed here.
    """
    if not cell_output_abs.exists():
        raise MediaCaptureError(
            target=target, cell_id=cell.cell_id,
            reason=(
                f"renderer succeeded but did not write {cell_output_abs}"
            ),
        )
    # Defense in depth: resolve symlinks and re-check the boundary.
    # A malicious renderer could write to a real path, then symlink
    # the cell path over a file outside the workspace; we catch it
    # here.
    try:
        real_output = cell_output_abs.resolve(strict=True)
    except OSError as exc:
        raise MediaCaptureError(
            target=target, cell_id=cell.cell_id,
            reason=f"failed to resolve {cell_output_abs}: {exc}",
        ) from exc
    if not real_output.is_relative_to(workspace_root):
        raise MediaCaptureError(
            target=target, cell_id=cell.cell_id,
            reason=(
                f"resolved cell output {real_output} is outside workspace root"
            ),
        )

    raw_bytes = real_output.read_bytes()
    if not raw_bytes:
        raise MediaCaptureError(
            target=target, cell_id=cell.cell_id,
            reason=f"renderer wrote an empty file at {real_output}",
        )
    if raw_bytes[:8] != _PNG_SIGNATURE:
        raise MediaCaptureError(
            target=target, cell_id=cell.cell_id,
            reason=(
                f"cell output {real_output} is not a PNG (missing signature)"
            ),
        )

    width, height = _png_dimensions(raw_bytes)
    if width is None or height is None:
        raise MediaCaptureError(
            target=target, cell_id=cell.cell_id,
            reason=(
                f"cell output {real_output} PNG IHDR is malformed or missing"
            ),
        )

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    size_bytes = len(raw_bytes)
    artifact_id = new_artifact_id()
    uri = build_media_uri(artifact_id)
    relpath = _relative_posix(real_output, root=workspace_root)

    params = _build_ledger_params(
        cell=cell,
        artifact_id=artifact_id,
        uri=uri,
        sha256=sha256,
        size_bytes=size_bytes,
        width=width,
        height=height,
        output_path=relpath,
        matrix_key=matrix_key,
    )
    result = MediaCaptureCellResult(
        cell=cell,
        artifact_id=artifact_id,
        uri=uri,
        sha256=sha256,
        size_bytes=size_bytes,
        width=width,
        height=height,
        output_path=relpath,
    )
    return (result, params)


__all__ = [
    "DEFAULT_OUTPUT_DIR_RELPATH",
    "DEFAULT_PER_CELL_TIMEOUT_SECONDS",
    "MediaCaptureCellResult",
    "MediaCaptureError",
    "MediaCaptureResult",
]
