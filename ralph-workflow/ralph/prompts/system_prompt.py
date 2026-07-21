"""System prompt materialization for supported agent transports."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pro_support.prompt import resolve_effective_prompt_path
from ralph.prompts.template_registry import _packaged_template_cache, packaged_template_root

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _write_system_prompt_file(
    system_prompt_path: Path,
    content: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    backend.mkdir(system_prompt_path.parent, parents=True, exist_ok=True)
    write_text_if_changed(backend, system_prompt_path, content, encoding="utf-8")


def materialize_system_prompt(
    *,
    workspace_root: Path,
    name: str,
    default_current_prompt: str | None = None,
    worker_namespace: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str:
    """Write a system prompt file for the named agent and return its path.

    The injected ``backend`` controls both ``mkdir`` and the physical
    text writes so a byte-identical re-emit of the current or system
    prompt does not advance the file's mtime or generate an additional
    fseventsd notification. The default backend is the real-Path
    backend; tests inject an in-memory counting backend to verify the
    idempotent skip. The post-condition "file contains the rendered
    prompt" always holds: any read uncertainty or content mismatch
    falls through to a real write.
    """
    current_prompt_path = _sync_current_prompt_file(
        workspace_root=workspace_root,
        default_current_prompt=default_current_prompt,
        worker_namespace=worker_namespace,
        backend=backend,
    )
    current_plan_path = _current_plan_handoff_path(workspace_root, phase_name=name)
    system_prompt_path = (
        worker_system_prompt_path(worker_namespace, name)
        if worker_namespace is not None
        else workspace_root / ".agent" / "tmp" / f"{name}_system_prompt.md"
    )
    _write_system_prompt_file(
        system_prompt_path,
        build_system_prompt(
            phase_name=name,
            current_prompt_path=str(current_prompt_path),
            current_plan_path=str(current_plan_path) if current_plan_path is not None else None,
        ),
        backend=backend,
    )
    return str(system_prompt_path)


def _sync_current_prompt_file(
    *,
    workspace_root: Path,
    default_current_prompt: str | None,
    worker_namespace: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> Path:
    """Mirror the operator-visible prompt into the engine-owned CURRENT_PROMPT.md.

    The source is resolved through
    :func:`ralph.pro_support.prompt.resolve_effective_prompt_path` so the
    ``PROMPT_PATH`` env var is honoured in Pro mode. The destination
    remains the engine-owned materialised file under ``.agent/``.

    The injected ``backend`` controls both ``mkdir`` and the physical
    text writes so a byte-identical re-emit of the current prompt does
    not advance the file's mtime or generate an additional fseventsd
    notification. The default backend is the real-Path backend;
    tests inject an in-memory counting backend to verify the
    idempotent skip. The post-condition "file contains the resolved
    prompt text" always holds: any read uncertainty or content mismatch
    falls through to a real write.
    """
    current_prompt_path = (
        worker_current_prompt_path(worker_namespace)
        if worker_namespace is not None
        else workspace_root / ".agent" / "CURRENT_PROMPT.md"
    )
    source_prompt_path = resolve_effective_prompt_path(workspace_root, os.environ)
    backend.mkdir(current_prompt_path.parent, parents=True, exist_ok=True)
    if source_prompt_path.exists():
        # Use the (st_size, st_mtime_ns) fast path to avoid reading
        # current_prompt_path's content when it is identical to
        # source_prompt_path (the common case after the first materialise).
        # The helper also returns the source text when needed.
        def _stat(path: Path) -> tuple[int, int] | None:
            if not path.exists():
                return None
            stat = path.stat()
            return (stat.st_size, stat.st_mtime_ns)

        changed, prompt_text = _prompt_files_differ(
            source_prompt_path,
            current_prompt_path,
            stat_fn=_stat,
            read_source=lambda p: p.read_text(encoding="utf-8"),
            read_current=lambda p: p.read_text(encoding="utf-8"),
        )
        # ``changed`` covers two cases: current is missing (size/mtime
        # don't match) OR the content actually differs. ``prompt_text``
        # is None only when sizes differ AND read_source was not
        # provided -- but we always pass read_source above, so it is
        # populated when changed is True.
        if changed and prompt_text is not None:
            write_text_if_changed(
                backend, current_prompt_path, prompt_text, encoding="utf-8"
            )
            if worker_namespace is None:
                _write_prompt_history_snapshot(
                    workspace_root=workspace_root,
                    prompt_text=prompt_text,
                    backend=backend,
                )
        return current_prompt_path
    if not backend.exists(current_prompt_path) and default_current_prompt is not None:
        write_text_if_changed(
            backend,
            current_prompt_path,
            default_current_prompt,
            encoding="utf-8",
        )
        if worker_namespace is None:
            _write_prompt_history_snapshot(
                workspace_root=workspace_root,
                prompt_text=default_current_prompt,
                backend=backend,
            )
    return current_prompt_path


def worker_current_prompt_path(worker_namespace: Path) -> Path:
    """Return the worker-local mirror path for CURRENT_PROMPT.md."""
    return worker_namespace / "tmp" / "CURRENT_PROMPT.md"


def worker_system_prompt_path(worker_namespace: Path, phase: str) -> Path:
    """Return the worker-local system prompt materialization path."""
    normalized = phase.replace("/", "_").replace(" ", "_")
    return worker_namespace / "tmp" / f"{normalized}_system_prompt.md"


def _write_prompt_history_snapshot(
    *,
    workspace_root: Path,
    prompt_text: str,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Mirror the new prompt into the timestamped prompt-history directory.

    The injected ``backend`` controls both ``mkdir`` and the physical
    text write so the implementation does not regress to a raw
    ``Path.write_text`` call. The history filename embeds
    ``_history_timestamp`` so the file is unique per call; the idempotent
    guard still holds even when the same nanosecond timestamps collide
    (the read-comparison falls through to a real write on a content
    mismatch or a missing path).
    """
    history_dir = workspace_root / ".agent" / "prompt_history"
    history_path = history_dir / f"PROMPT_{_history_timestamp()}.md"
    backend.mkdir(history_dir, parents=True, exist_ok=True)
    write_text_if_changed(backend, history_path, prompt_text, encoding="utf-8")


def _history_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _current_plan_handoff_path(workspace_root: Path, *, phase_name: str) -> Path | None:
    if phase_name == "planning":
        return None
    plan_path = workspace_root / ".agent" / "PLAN.md"
    return plan_path if plan_path.exists() else None


def build_system_prompt(
    *,
    phase_name: str,
    current_prompt_path: str,
    current_plan_path: str | None = None,
) -> str:
    """Build the system prompt text that points the agent at durable task context files."""
    unattended = _unattended_mode_text().strip()
    if phase_name == "planning":
        return (
            f"{unattended}\n\n"
            "Use the canonical task request from this file:\n"
            f"`{current_prompt_path}`\n\n"
            "Treat that file as the source of truth for the current goal.\n"
            "Do not ask the user to restate it.\n"
        )

    plan_guidance = ""
    if current_plan_path is not None:
        plan_guidance = (
            "\n"
            "Use the canonical plan from this file whenever it exists:\n"
            f"`{current_plan_path}`\n\n"
            "Treat that file as the source of truth for the current goal and execution steps, "
            "especially after any context compaction, resume, or continuation.\n"
        )
    return (
        f"{unattended}\n\n"
        "Use the canonical task context from this file:\n"
        f"`{current_prompt_path}`\n\n"
        "Treat that file as background context for the current task.\n"
        "Do not ask the user to restate it.\n"
        f"{plan_guidance}"
    )


def _unattended_mode_text() -> str:
    return _packaged_template_cache.get(
        "shared/_unattended_mode.jinja", root=packaged_template_root()
    )


def _prompt_files_differ(
    source: Path,
    current: Path,
    *,
    stat_fn: Callable[[Path], tuple[int, int] | None],
    read_source: Callable[[Path], str] | None = None,
    read_current: Callable[[Path], str] | None = None,
) -> tuple[bool, str | None]:
    """Compare two prompt files using a (st_size, st_mtime_ns) fast path.

    Returns ``(changed, source_text_if_read)``. The fast-path rules are:

      - ``current`` does not exist -> ``(True, source_text)``. Source IS
        read so the caller can write it to ``current``.
      - ``(src.st_size, src.st_mtime_ns) == (cur.st_size, cur.st_mtime_ns)``
        -> ``(False, None)``. NO content reads.
      - sizes differ -> ``(True, source_text_if_read)``. CURRENT is
        never read; source IS read only if the caller passed
        ``read_source``.
      - sizes match but mtime differs -> read both, return
        ``(src_text != cur_text, src_text)``.

    ``stat_fn`` is an injectable callable returning ``(size, mtime_ns)``
    or ``None`` if the path does not exist. ``read_source`` /
    ``read_current`` are optional injectable readers (default
    ``Path.read_text``). All I/O is funnelled through these seams so
    tests can run without real disk I/O.

    Used by both ``_sync_current_prompt_file`` (which always needs the
    source text to rematerialise) and
    ``pipeline.prompt_prep._prompt_changed_since_last_materialization``
    (which only needs the boolean verdict).
    """

    def _default_read(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    read_source_fn = read_source if read_source is not None else _default_read
    read_current_fn = read_current if read_current is not None else _default_read

    source_stat = stat_fn(source)
    current_stat = stat_fn(current)
    if source_stat is None:
        return False, None
    if current_stat is None:
        return True, read_source_fn(source)
    src_size, src_mtime = source_stat
    cur_size, cur_mtime = current_stat
    if src_size == cur_size and src_mtime == cur_mtime:
        return False, None
    if src_size != cur_size:
        return True, (read_source_fn(source) if read_source is not None else None)
    src_text = read_source_fn(source)
    cur_text = read_current_fn(current)
    return src_text != cur_text, src_text
