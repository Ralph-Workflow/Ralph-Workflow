
from __future__ import annotations

import hashlib
import json
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ralph.executor._process_result import ProcessResult
from ralph.executor._process_run_options import ProcessRunOptions
from ralph.executor.process import run_process
from ralph.mcp.multimodal._media_entry_extras import MediaEntryExtras
from ralph.mcp.multimodal.resources import (
    MediaManifest,
    build_media_uri,
    new_artifact_id,
)
from ralph.mcp.server._wire_ledger import append_wire_record
from ralph.visual.capture_request import CaptureRequest
from ralph.visual.policy_facts import MAX_CAPTURE_COMMAND_TOKENS, parse_policy_facts

_CAPTURE_POLICY_RELPATH: str = "docs/ralph-workflow-policy/design-system-policy.md"

if TYPE_CHECKING:
    from ralph.mcp.tools.coordination import CoordinationSessionLike
    from ralph.visual.capture_cell import CaptureCell
    from ralph.workspace.protocol import Workspace


DEFAULT_PER_CELL_TIMEOUT_SECONDS: float = 30.0

DEFAULT_OUTPUT_DIR_RELPATH: str = ".agent/tmp/visual-captures"

_PNG_SIGNATURE: bytes = b"\x89PNG\r\n\x1a\n"

_PNG_HEADER_SIZE: int = 24

_TARGET_PLACEHOLDER: str = "{target}"

_CAPTURE_OUTPUT_ENV: str = "RALPH_CAPTURE_OUTPUT"
_CAPTURE_TARGET_ENV: str = "RALPH_CAPTURE_TARGET"
_CAPTURE_VIEWPORT_ENV: str = "RALPH_CAPTURE_VIEWPORT"
_CAPTURE_VIEWPORT_WIDTH_ENV: str = "RALPH_CAPTURE_VIEWPORT_WIDTH"
_CAPTURE_VIEWPORT_HEIGHT_ENV: str = "RALPH_CAPTURE_VIEWPORT_HEIGHT"
_CAPTURE_THEME_ENV: str = "RALPH_CAPTURE_THEME"
_CAPTURE_STATE_ENV: str = "RALPH_CAPTURE_STATE"
_CAPTURE_CELL_ID_ENV: str = "RALPH_CAPTURE_CELL_ID"




class MediaCaptureError(RuntimeError):

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




class _ExecutorLike(Protocol):

    def __call__(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        options: ProcessRunOptions | None = None,
    ) -> object: ...




@dataclass(frozen=True)
class _MediaCaptureCellResult:

    cell: CaptureCell
    artifact_id: str
    uri: str
    sha256: str
    size_bytes: int
    width: int | None
    height: int | None
    output_path: str


_MediaCaptureCellResult.__name__ = "MediaCaptureCellResult"
_MediaCaptureCellResult.__qualname__ = "MediaCaptureCellResult"
MediaCaptureCellResult = _MediaCaptureCellResult


@dataclass(frozen=True)
class _MediaCaptureResult:

    target: str
    matrix_key: str
    cells: tuple[_MediaCaptureCellResult, ...]


_MediaCaptureResult.__name__ = "MediaCaptureResult"
_MediaCaptureResult.__qualname__ = "MediaCaptureResult"
MediaCaptureResult = _MediaCaptureResult




def _png_dimensions(raw_bytes: bytes) -> tuple[int | None, int | None]:
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




def _resolve_command(*, design_capture_command: str, target: str) -> tuple[str, tuple[str, ...]]:
    if not isinstance(design_capture_command, str) or not design_capture_command.strip():
        raise MediaCaptureError(
            target=target,
            cell_id="<command>",
            reason="design_capture_command must be a non-empty string",
        )
    if not isinstance(target, str) or not target.strip():
        raise MediaCaptureError(
            target="<unresolved>",
            cell_id="<command>",
            reason="capture target must be a non-empty string",
        )
    if _TARGET_PLACEHOLDER in design_capture_command:
        resolved = design_capture_command.replace(_TARGET_PLACEHOLDER, target)
        try:
            tokens = shlex.split(resolved)
        except ValueError as exc:
            raise MediaCaptureError(
                target=target,
                cell_id="<command>",
                reason=f"design_capture_command is not shell-parseable: {exc}",
            ) from exc
        if not tokens:
            raise MediaCaptureError(
                target=target,
                cell_id="<command>",
                reason="design_capture_command resolved to zero tokens",
            )
        if len(tokens) > MAX_CAPTURE_COMMAND_TOKENS:
            raise MediaCaptureError(
                target=target,
                cell_id="<command>",
                reason="design_capture_command declares too many argv tokens",
            )
        return (tokens[0], tuple(tokens[1:]))
    try:
        tokens = shlex.split(design_capture_command)
    except ValueError as exc:
        raise MediaCaptureError(
            target=target,
            cell_id="<command>",
            reason=f"design_capture_command is not shell-parseable: {exc}",
        ) from exc
    if not tokens:
        raise MediaCaptureError(
            target=target,
            cell_id="<command>",
            reason="design_capture_command resolved to zero tokens",
        )
    if len(tokens) + 1 > MAX_CAPTURE_COMMAND_TOKENS:
        raise MediaCaptureError(
            target=target,
            cell_id="<command>",
            reason="design_capture_command declares too many argv tokens",
        )
    return (tokens[0], (*tuple(tokens[1:]), target))


def _cell_output_path(*, output_dir_abs: Path, cell_id: str) -> Path:
    return output_dir_abs / f"{cell_id}.png"


def _relative_posix(path: Path, *, root: Path) -> str:
    return path.relative_to(root).as_posix()




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
        raise ValueError(
            f"output_dir_relpath={output_dir_relpath!r} resolves outside the workspace root"
        )
    output_dir_abs.mkdir(parents=True, exist_ok=True)

    target = capture_request.target
    matrix_key = _matrix_key_for(capture_request)
    results: list[_MediaCaptureCellResult] = []
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


def register_media_capture_result(
    manifest: MediaManifest,
    *,
    workspace_root: Path,
    result: MediaCaptureResult,
) -> None:
    for cell in result.cells:
        source_path = cell.output_path
        output_path = workspace_root / source_path

        def load_capture(path: Path = output_path) -> bytes | None:
            try:
                return path.read_bytes()
            except OSError:
                return None

        entry = manifest.add(
            title=Path(source_path).name,
            mime_type="image/png",
            modality="image",
            raw_bytes=b"",
            extras=MediaEntryExtras(
                artifact_id=cell.artifact_id,
                identity_key=f"capture:{cell.artifact_id}",
                source_path=source_path,
                byte_loader=load_capture,
            ),
        )
        if entry.uri != cell.uri:
            raise MediaCaptureError(
                target=result.target,
                cell_id=cell.cell.cell_id,
                reason="capture manifest registration changed the minted artifact URI",
            )


def handle_media_capture_tool(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> object:
    from ralph.mcp.tools.coordination import (
        InvalidParamsError,
        ToolContent,
        ToolResult,
        require_capability,
    )
    from ralph.mcp.tools.workspace._media_session import _get_media_manifest
    from ralph.mcp.tools.workspace._utils import MEDIA_CAPTURE_CAPABILITY

    require_capability(session, MEDIA_CAPTURE_CAPABILITY, "Media capture")
    target = params.get("target")
    if not isinstance(target, str) or not target.strip():
        raise InvalidParamsError("media_capture requires a non-empty target")
    if session.broker_secret is None:
        raise InvalidParamsError(
            "media_capture requires a broker secret so every capture is wire-ledger-backed"
        )
    workspace_root = Path(workspace.absolute_path(""))
    try:
        policy_markdown = workspace.read(_CAPTURE_POLICY_RELPATH)
        facts = parse_policy_facts(policy_markdown, target=target)
    except (OSError, ValueError) as exc:
        raise InvalidParamsError(
            "media_capture requires a resolved design-system policy with declared "
            f"renderer and matrix facts: {exc}"
        ) from exc
    request = CaptureRequest.build(
        target=facts.target,
        viewports=facts.viewports,
        themes=facts.themes,
        states=facts.states,
    )
    result = handle_media_capture(
        workspace_root,
        run_id=session.run_id,
        capture_request=request,
        design_capture_command=facts.design_capture_command,
        secret=session.broker_secret,
    )
    manifest = _get_media_manifest(session)
    if manifest is None:
        raise InvalidParamsError(
            "media_capture requires an active session manifest for replayable captures"
        )
    register_media_capture_result(manifest, workspace_root=workspace_root, result=result)
    payload = {
        "target": result.target,
        "matrix_key": result.matrix_key,
        "captures": [
            {
                "cell_id": cell.cell.cell_id,
                "uri": cell.uri,
                "sha256": cell.sha256,
                "width": cell.width,
                "height": cell.height,
            }
            for cell in result.cells
        ],
    }
    return ToolResult(
        content=[ToolContent.text_content(json.dumps(payload, separators=(",", ":")))],
        is_error=False,
    )


def _matrix_key_for(capture_request: CaptureRequest) -> str:
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
    cell_output_abs = _cell_output_path(output_dir_abs=output_dir_abs, cell_id=cell.cell_id)
    if cell_output_abs.exists() or cell_output_abs.is_symlink():
        cell_output_abs.unlink()
    if not cell_output_abs.is_relative_to(workspace_root):
        raise MediaCaptureError(
            target=target,
            cell_id=cell.cell_id,
            reason=(f"cell output path {cell_output_abs} escapes the workspace root"),
        )

    env = _build_cell_env(
        target=target,
        cell=cell,
        cell_output_abs=cell_output_abs,
    )
    command, args = _resolve_command(
        design_capture_command=design_capture_command,
        target=target,
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
            target=target,
            cell_id=cell.cell_id,
            reason=f"renderer invocation raised: {exc}",
        ) from exc

    if not isinstance(result, ProcessResult):
        raise MediaCaptureError(
            target=target,
            cell_id=cell.cell_id,
            reason=(f"executor returned {type(result).__name__}; expected ProcessResult"),
        )
    if not result.succeeded:
        raise MediaCaptureError(
            target=target,
            cell_id=cell.cell_id,
            reason=(
                f"renderer exited with returncode={result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip() or '<no output>'}"
            ),
            returncode=result.returncode,
        )

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
    if not cell_output_abs.exists():
        raise MediaCaptureError(
            target=target,
            cell_id=cell.cell_id,
            reason=(f"renderer succeeded but did not write {cell_output_abs}"),
        )
    try:
        real_output = cell_output_abs.resolve(strict=True)
    except OSError as exc:
        raise MediaCaptureError(
            target=target,
            cell_id=cell.cell_id,
            reason=f"failed to resolve {cell_output_abs}: {exc}",
        ) from exc
    if not real_output.is_relative_to(workspace_root):
        raise MediaCaptureError(
            target=target,
            cell_id=cell.cell_id,
            reason=(f"resolved cell output {real_output} is outside workspace root"),
        )

    raw_bytes = real_output.read_bytes()
    if not raw_bytes:
        raise MediaCaptureError(
            target=target,
            cell_id=cell.cell_id,
            reason=f"renderer wrote an empty file at {real_output}",
        )
    if raw_bytes[:8] != _PNG_SIGNATURE:
        raise MediaCaptureError(
            target=target,
            cell_id=cell.cell_id,
            reason=(f"cell output {real_output} is not a PNG (missing signature)"),
        )

    width, height = _png_dimensions(raw_bytes)
    if width is None or height is None:
        raise MediaCaptureError(
            target=target,
            cell_id=cell.cell_id,
            reason=(f"cell output {real_output} PNG IHDR is malformed or missing"),
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
    "handle_media_capture",
    "handle_media_capture_tool",
    "register_media_capture_result",
]
