"""Workspace utility functions, constants, and helpers."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, NamedTuple, cast

from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    ToolError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.workspace import Workspace

WORKSPACE_READ_CAPABILITY = "WorkspaceRead"
WORKSPACE_WRITE_TRACKED_CAPABILITY = "WorkspaceWriteTracked"
WORKSPACE_WRITE_EPHEMERAL_CAPABILITY = "WorkspaceWriteEphemeral"
WORKSPACE_METADATA_READ_CAPABILITY = "WorkspaceMetadataRead"
WORKSPACE_EDIT_CAPABILITY = "WorkspaceEdit"
WORKSPACE_DELETE_CAPABILITY = "WorkspaceDelete"
MEDIA_READ_CAPABILITY = "media.read"

_SUPPORTED_IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_GREP_DEFAULT_LIMIT = 1000
_MAX_PATTERN_LENGTH = 1000
FULL_READ_DEFAULT_MAX_BYTES = 5_000_000


def _attribute_value(
    obj: object, attribute_name: str, default: object | None = None
) -> object | None:
    return cast("object | None", getattr(obj, attribute_name, default))


def required_string_param(params: dict[str, object], name: str) -> str:
    """Return a required string parameter, raising if it is missing."""
    value = params.get(name)
    if not isinstance(value, str):
        raise InvalidParamsError(f"Missing '{name}' parameter")
    return value


def _tool_json(data: dict[str, object]) -> str:
    """Serialize a result dict to a JSON string for ToolResult content."""
    return json.dumps(data)


def _int_param(params: dict[str, object], name: str, default: int = 0) -> int:
    """Extract an int parameter from params dict with a default."""
    value = params.get(name, default)
    if isinstance(value, int):
        return value
    return int(str(value))


def _int_opt_param(params: dict[str, object], name: str) -> int | None:
    """Extract an optional int parameter from params dict."""
    value = params.get(name)
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(str(value))


def normalize_relative_path(path: str) -> str:
    """Return a normalized POSIX-style relative path string.

    Collapses redundant separators and parent references. An empty or
    dot-only path resolves to ``""`` so callers can treat it as the
    workspace root.
    """
    normalized = str(PurePosixPath(path))
    if normalized in ("", "."):
        return ""
    return normalized


def join_path(base: str, entry: str) -> str:
    """Join a base relative path with an entry and normalize the result.

    Args:
        base: Existing relative path, or ``""`` for the workspace root.
        entry: Path fragment to append.

    Returns:
        A normalized POSIX-style relative path.
    """
    if not base:
        return normalize_relative_path(entry)
    return normalize_relative_path(str(PurePosixPath(base) / entry))


def list_dir_entries(workspace: Workspace, path: str) -> list[str]:
    """List the entries in ``path`` and surface workspace errors as ToolError."""
    try:
        return workspace.list_dir(path)
    except Exception as exc:
        raise ToolError(f"Failed to list directory '{path}': {exc}") from exc


def is_parallel_worker(session: object) -> bool:
    """Return True when the active session is a parallel fan-out worker."""
    flag = _attribute_value(session, "is_parallel_worker", False)
    if callable(flag):
        try:
            executable = cast("Callable[[], object]", flag)
            return bool(executable())
        except TypeError:
            return False
    return bool(flag)


def check_edit_area_restriction(session: object, path: str) -> None:
    """Enforce the parallel-worker edit-area restriction for ``path``.

    Raises:
        CapabilityDeniedError: If the session is a parallel worker and the
            configured edit-area policy does not approve the path.
    """
    if not is_parallel_worker(session):
        return
    checker = _attribute_value(session, "check_edit_area")
    if not callable(checker):
        return
    callable_checker = cast("Callable[[str], object]", checker)
    outcome = callable_checker(path)
    if is_policy_approved(outcome):
        return
    raise CapabilityDeniedError(f"Write to '{path}' denied: edit area restriction")


def _write_file_to_workspace(workspace: Workspace, path: str, content: str) -> None:
    try:
        workspace.write(path, content)
    except Exception as exc:
        raise ToolError(f"Failed to write file '{path}': {exc}") from exc


def is_path_git_tracked(workspace: Workspace, path: str) -> bool:
    """Return True when ``path`` should be treated as git-tracked output.

    The path must exist in the workspace and must not live in ephemeral
    directories such as ``.agent/``, ``target/``, or ``node_modules/``.
    """
    normalized = normalize_relative_path(path)
    if not normalized:
        return False
    try:
        exists = workspace.exists(normalized)
    except ValueError:
        return False
    if not exists:
        return False
    candidate = normalized.replace("\\", "/")
    return (
        ".agent/" not in candidate
        and "/target/" not in candidate
        and "node_modules/" not in candidate
    )


def infer_image_mime_type(path: str) -> str | None:
    """Return the MIME type for supported image paths based on extension."""
    suffix = PurePosixPath(path).suffix.lower()
    return _SUPPORTED_IMAGE_MIME_TYPES.get(suffix)


class _ReadSelector(NamedTuple):
    """Normalized partial-read selectors for read_file."""

    start: int | None
    end: int | None
    off: int | None
    lim: int | None
    head: int | None
    tail: int | None

    @classmethod
    def from_params(cls, params: dict[str, object]) -> _ReadSelector:
        """Extract and normalize selectors from raw MCP params.

        Treats 0 as absent for all params except offset (offset=0 is a valid
        start-of-file position). Inert zero defaults sent by brokers are
        normalized to None so they do not trigger mode selection.
        """

        def _n(v: int | None) -> int | None:
            return None if v == 0 else v

        return cls(
            start=_n(_int_opt_param(params, "line_start")),
            end=_n(_int_opt_param(params, "line_end")),
            off=_int_opt_param(params, "offset"),
            lim=_n(_int_opt_param(params, "limit")),
            head=_n(_int_opt_param(params, "head")),
            tail=_n(_int_opt_param(params, "tail")),
        )

    def is_active(self) -> bool:
        """Return True when at least one partial-read mode is requested."""
        line_range = (self.start is not None) or (self.end is not None)
        byte_window = (self.off is not None and self.off > 0) or (self.lim is not None)
        return line_range or byte_window or (self.head is not None) or (self.tail is not None)
