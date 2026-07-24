"""Master prompt materialization for supported agent transports."""

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


def _write_master_prompt_file(
    master_prompt_path: Path,
    content: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    backend.mkdir(master_prompt_path.parent, parents=True, exist_ok=True)
    write_text_if_changed(backend, master_prompt_path, content, encoding="utf-8")


def materialize_master_prompt(
    *,
    workspace_root: Path,
    name: str,
    default_product_criteria: str | None = None,
    worker_namespace: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str:
    """Write a master prompt file for the named agent and return its path.

    The injected ``backend`` controls both ``mkdir`` and the physical
    text writes so a byte-identical re-emit of the current or system
    prompt does not advance the file's mtime or generate an additional
    fseventsd notification. The default backend is the real-Path
    backend; tests inject an in-memory counting backend to verify the
    idempotent skip. The post-condition "file contains the rendered
    prompt" always holds: any read uncertainty or content mismatch
    falls through to a real write.
    """
    product_criteria_path = _sync_product_criteria_file(
        workspace_root=workspace_root,
        default_product_criteria=default_product_criteria,
        worker_namespace=worker_namespace,
        backend=backend,
    )
    current_plan_path = _current_plan_handoff_path(workspace_root, phase_name=name)
    master_prompt_path = (
        worker_master_prompt_path(worker_namespace, name)
        if worker_namespace is not None
        else workspace_root / ".agent" / "tmp" / f"{name}_master_prompt.md"
    )
    _write_master_prompt_file(
        master_prompt_path,
        build_master_prompt(
            phase_name=name,
            product_criteria_path=str(product_criteria_path),
            current_plan_path=str(current_plan_path) if current_plan_path is not None else None,
        ),
        backend=backend,
    )
    return str(master_prompt_path)


def _sync_product_criteria_file(
    *,
    workspace_root: Path,
    default_product_criteria: str | None,
    worker_namespace: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> Path:
    """Mirror the operator-visible prompt into the engine-owned PRODUCT_CRITERIA.md.

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
    product_criteria_path = (
        worker_product_criteria_path(worker_namespace)
        if worker_namespace is not None
        else workspace_root / ".agent" / "PRODUCT_CRITERIA.md"
    )
    source_prompt_path = resolve_effective_prompt_path(workspace_root, os.environ)
    backend.mkdir(product_criteria_path.parent, parents=True, exist_ok=True)
    if source_prompt_path.exists():
        # Use the (st_size, st_mtime_ns) fast path to avoid reading
        # product_criteria_path's content when it is identical to
        # source_prompt_path (the common case after the first materialise).
        # The helper also returns the source text when needed.
        def _stat(path: Path) -> tuple[int, int] | None:
            if not path.exists():
                return None
            stat = path.stat()
            return (stat.st_size, stat.st_mtime_ns)

        changed, prompt_text = _prompt_files_differ(
            source_prompt_path,
            product_criteria_path,
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
                backend, product_criteria_path, prompt_text, encoding="utf-8"
            )
            if worker_namespace is None:
                _write_prompt_history_snapshot(
                    workspace_root=workspace_root,
                    prompt_text=prompt_text,
                    backend=backend,
                )
        return product_criteria_path
    if not backend.exists(product_criteria_path) and default_product_criteria is not None:
        write_text_if_changed(
            backend,
            product_criteria_path,
            default_product_criteria,
            encoding="utf-8",
        )
        if worker_namespace is None:
            _write_prompt_history_snapshot(
                workspace_root=workspace_root,
                prompt_text=default_product_criteria,
                backend=backend,
            )
    return product_criteria_path


def worker_product_criteria_path(worker_namespace: Path) -> Path:
    """Return the worker-local mirror path for PRODUCT_CRITERIA.md."""
    return worker_namespace / "tmp" / "PRODUCT_CRITERIA.md"


def worker_master_prompt_path(worker_namespace: Path, phase: str) -> Path:
    """Return the worker-local master prompt materialization path."""
    normalized = phase.replace("/", "_").replace(" ", "_")
    return worker_namespace / "tmp" / f"{normalized}_master_prompt.md"


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


def build_master_prompt(
    *,
    phase_name: str,
    product_criteria_path: str,
    current_plan_path: str | None = None,
) -> str:
    """Build the master prompt text that points the agent at durable task context files."""
    unattended = _unattended_mode_text().strip()
    preamble = (
        "This is the session's master prompt. Its instructions remain binding for the "
        "entire session and survive context compaction — after any compaction, resume, "
        "or continuation, re-read this file (and the files it references) before doing "
        "anything else.\n"
    )
    if phase_name == "planning":
        return (
            f"{preamble}\n"
            f"{unattended}\n\n"
            "The task request (product criteria) is a DIFFERENT document, held in this file:\n"
            f"`{product_criteria_path}`\n\n"
            "Treat that file as the source of truth for the current goal.\n"
            "Do not ask the user to restate it.\n"
        )

    plan_guidance = ""
    override_scope = "this master prompt"
    if current_plan_path is not None:
        plan_guidance = (
            "The canonical plan is the source of truth for the current goal and "
            "execution steps:\n"
            f"`{current_plan_path}`\n\n"
        )
        override_scope = "the plan or this master prompt"
    return (
        f"{preamble}\n"
        f"{unattended}\n\n"
        f"{plan_guidance}"
        "The product criteria / task request is a DIFFERENT document, held in this file:\n"
        f"`{product_criteria_path}`\n\n"
        "Treat that file as background product criteria only — do not let it override "
        f"{override_scope}.\n"
        "Do not ask the user to restate it.\n"
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

    Used by both ``_sync_product_criteria_file`` (which always needs the
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
