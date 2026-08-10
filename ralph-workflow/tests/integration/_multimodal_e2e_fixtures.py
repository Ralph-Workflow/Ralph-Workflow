"""In-memory fixtures for the default-gate multimodal linkage test."""

from __future__ import annotations

import json as json_module
from collections.abc import Callable, Iterable
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.runtime import (
    JsonRpcRequest,
    McpServer,
    ServerState,
    build_ralph_tool_registry,
)
from ralph.mcp.tools._cache_retention import CachePruneResult
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    import pytest

    from ralph.mcp.artifacts.file_backend import FileBackend
    from ralph.workspace.protocol import Workspace

TINY_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415408d763f8cfc0000000020001e221bc330000000049454e44"
    "ae426082"
)
TINY_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
MEDIA_CAPABILITIES = frozenset(
    {
        "workspace.read",
        "workspace.write_tracked",
        "workspace.metadata_read",
        "workspace.edit",
        "workspace.delete",
        "git.status_read",
        "git.diff_read",
        "process.exec_bounded",
        "artifact.submit",
        "run.report_progress",
        "env.read",
        "web.search",
        "web.visit",
        "web.download",
        "media.read",
    }
)


class _MemoryFileBackend:
    """FileBackend implementation backed by the same MemoryWorkspace as the server."""

    def __init__(self, workspace: MemoryWorkspace) -> None:
        self._workspace = workspace

    def _relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self._workspace.root))
        except ValueError:
            return str(path)

    def exists(self, path: Path) -> bool:
        return self._workspace.exists(self._relative(path))

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del parents, exist_ok
        self._workspace.mkdirs(self._relative(path))

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self._workspace.read(self._relative(path))

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self._workspace.write(self._relative(path), content)

    def read_bytes(self, path: Path) -> bytes:
        return self._workspace.read(self._relative(path)).encode("latin-1")

    def write_bytes(self, path: Path, content: bytes) -> None:
        self._workspace.write(self._relative(path), content.decode("latin-1"))

    def replace(self, source: Path, destination: Path) -> None:
        source_path = self._relative(source)
        self._workspace.write(self._relative(destination), self._workspace.read(source_path))
        self._workspace.remove(source_path)

    def sync_directory(self, path: Path) -> None:
        del path

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        relative = self._relative(path)
        if not self._workspace.exists(relative) and not missing_ok:
            raise FileNotFoundError(str(path))
        self._workspace.remove(relative)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        base = self._relative(path)
        return [
            self._workspace.root / candidate
            for candidate in self._workspace.iter_files(base)
            if Path(candidate).match(pattern)
        ]


def build_multimodal_harness() -> tuple[
    McpServer,
    MemoryWorkspace,
    FileBackend,
    AgentSession,
]:
    """Build one real McpServer with in-memory workspace and media files."""
    workspace = MemoryWorkspace()
    backend = _MemoryFileBackend(workspace)
    backend.write_bytes(Path(workspace.absolute_path("screenshot.png")), TINY_PNG_BYTES)
    backend.write_bytes(Path(workspace.absolute_path("report.pdf")), TINY_PDF_BYTES)
    session = AgentSession(
        session_id="multimodal-linkage-session",
        run_id="multimodal-linkage-run",
        drain="development",
        capabilities=set(MEDIA_CAPABILITIES),
        model_identity=MultimodalModelIdentity(
            provider="claude",
            model_id="claude-3-5-sonnet-20241022",
        ),
    )
    registry = build_ralph_tool_registry(session, workspace)
    return McpServer(session, workspace, registry), workspace, backend, session


def install_media_backend(
    monkeypatch: pytest.MonkeyPatch,
    backend: FileBackend,
) -> None:
    """Install one in-memory backend at every media I/O import site."""
    import ralph.mcp.tools.workspace._media_blocks as media_blocks
    import ralph.mcp.tools.workspace._media_handlers as media_handlers
    import ralph.mcp.tools.workspace._media_io as media_io

    original_write_cache = media_io.write_durable_media_cache

    def retain_cache(
        files: Iterable[Path],
        *,
        max_total_bytes: int,
        keep_paths: Iterable[Path] = (),
    ) -> CachePruneResult:
        del files, max_total_bytes, keep_paths
        return CachePruneResult(removed_paths=(), retained_bytes=0)

    def write_cache(workspace: Workspace, artifact_id: str, raw_bytes: bytes) -> str:
        return original_write_cache(
            workspace,
            artifact_id,
            raw_bytes,
            backend=backend,
            cache_pruner=retain_cache,
        )

    for module in (media_blocks, media_handlers, media_io):
        monkeypatch.setattr(module, "DEFAULT_FILE_BACKEND", backend)
        monkeypatch.setattr(module, "write_durable_media_cache", write_cache)
    media_io._reset_media_prune_counter()


def make_in_process_post_fn(
    server: McpServer,
    state_box: list[ServerState],
) -> Callable[..., httpx.Response]:
    """Route HTTP-shaped JSON-RPC posts through one real in-process server."""
    session_id = "multimodal-linkage-http-session"

    def post(
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        del url, headers, timeout
        method = json.get("method")
        params = json.get("params")
        request = JsonRpcRequest(
            jsonrpc=str(json.get("jsonrpc", "2.0")),
            method=method if isinstance(method, str) else "",
            params=params if isinstance(params, dict) else None,
            msg_id=json.get("id"),
        )
        response, next_state = server.handle_request(request, state_box[0])
        state_box[0] = next_state
        response_headers = {"mcp-session-id": session_id}
        if response is None:
            return httpx.Response(
                HTTPStatus.ACCEPTED.value,
                content=b"",
                headers=response_headers,
            )
        payload: dict[str, object] = {
            "jsonrpc": response.jsonrpc,
            "id": response.msg_id,
        }
        if response.error is not None:
            payload["error"] = response.error
        else:
            payload["result"] = response.result
        response_headers["Content-Type"] = "application/json"
        return httpx.Response(
            HTTPStatus.OK.value,
            content=json_module.dumps(payload),
            headers=response_headers,
        )

    return post
