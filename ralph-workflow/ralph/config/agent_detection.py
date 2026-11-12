from __future__ import annotations

import logging
import os
import re
import shutil
import tomllib
from contextlib import suppress
from pathlib import Path
from re import Match
from typing import TYPE_CHECKING, Literal, cast

from loguru import logger

from ralph.agents.builtin import builtin_supports
from ralph.config._agent_overrides import opencode_binary_override
from ralph.config.agents_file_writer import write_agents_toml
from ralph.config.bootstrap import resolve_global_config_dir
from ralph.git.errors import GitOperationError
from ralph.git.operations import create_commit, find_repo_root, stage_files
from ralph.git.scoped_auto_commit import (
    ScopedCommitResult,
    ScopedCommitStatus,
    capture_pre_write_contents,
    commit_deterministic_writes,
)
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    from ralph.git.commit_result import CommitCreationResult

    class _CreateCommitFn(Protocol):
        def __call__(
            self, repo_root: Path | str, message: str, *, expected_head: str
        ) -> CommitCreationResult: ...


__all__ = ["detect_installed_agents", "opencode_binary_override"]

_std_logger = logging.getLogger(__name__)


def _commit_deterministic_config_write(
    config_path: Path,
    subject: str,
    *,
    write_fn: Callable[[], object],
    create_commit_fn: _CreateCommitFn | None = None,
    stage_fn: Callable[[Path | str, list[str]], None] | None = None,
) -> ScopedCommitResult | None:
    cc = create_commit_fn if create_commit_fn is not None else create_commit
    sf = stage_fn if stage_fn is not None else stage_files
    repo_root_search_anchor = config_path if config_path.exists() else config_path.parent
    try:
        repo_root = find_repo_root(repo_root_search_anchor)
    except (GitOperationError, OSError) as exc:
        logger.debug(
            "config write at {} not eligible for auto-commit ({}); skipping",
            config_path,
            exc,
        )
        write_fn()
        return None
    resolved_config = config_path.resolve()
    abs_config = config_path if config_path.is_absolute() else (Path.cwd() / config_path)
    try:
        rel_path = abs_config.relative_to(repo_root).as_posix()
    except ValueError:
        logger.debug(
            "config write at {} resolves outside the git repo at {}; skipping auto-commit",
            resolved_config,
            repo_root,
        )
        write_fn()
        return None
    candidate_rel: list[str] = [rel_path]
    resolved_rel: str | None = None
    if abs_config.is_symlink():
        try:
            resolved_abs = abs_config.resolve()
            resolved_rel = resolved_abs.relative_to(repo_root).as_posix()
        except (OSError, ValueError):
            resolved_rel = None
        if resolved_rel is not None and resolved_rel != rel_path:
            candidate_rel.append(resolved_rel)
    pre_contents = capture_pre_write_contents(repo_root, candidate_rel)
    write_fn()
    result = commit_deterministic_writes(
        repo_root,
        paths=tuple(candidate_rel),
        pre_contents=pre_contents,
        subject=subject,
        create_commit_fn=cc,
        stage_fn=sf,
    )
    _log_commit_result(result, resolved_config, rel_path)
    return result


def _log_commit_result(result: ScopedCommitResult, config_path: Path, rel_path: str) -> None:
    if result.status is ScopedCommitStatus.CREATED:
        logger.debug(
            "config write at {} auto-committed: subject head={}",
            config_path,
            (result.sha or "")[:8],
        )
        return
    if result.status is ScopedCommitStatus.NOOP:
        logger.debug("config write at {} produced no diff; no auto-commit", config_path)
        return
    if result.status is ScopedCommitStatus.NOT_REPO:
        logger.debug("config write at {} not in a repo; auto-commit skipped", config_path)
        return
    if result.status is ScopedCommitStatus.SKIPPED:
        logger.warning(
            "config write at {} was already dirty at HEAD; auto-commit skipped "
            "(path: {}, dirty-skipped: {})",
            config_path,
            rel_path,
            ", ".join(result.skipped_paths) or rel_path,
        )
        return
    if result.status is ScopedCommitStatus.FAILED:
        logger.error(
            "config write at {} auto-commit FAILED ({}): path={}; "
            "the file is on disk but the chore commit was NOT created",
            config_path,
            result.error or "no error detail",
            rel_path,
        )


def _binary_for(name: str, cmd: str) -> str:
    override_name = {"agy": "RALPH_AGY_BINARY", "cursor": "RALPH_CURSOR_BINARY"}.get(name)
    override = (
        opencode_binary_override()
        if name == "opencode"
        else os.environ.get(override_name)
        if override_name is not None
        else None
    )
    return (override or cmd).split(maxsplit=1)[0]


def detect_installed_agents() -> list[str]:
    return [
        support.name
        for support in builtin_supports()
        if shutil.which(_binary_for(support.name, support.cmd)) is not None
    ]


_AUTOWIRE_TRIGGER = "ralph --init: autowire agent chains"


def _agents_config_filename(name: str) -> bool:
    from ralph.config._config_filenames import _GLOBAL_AGENTS_FILENAME

    return name in {_GLOBAL_AGENTS_FILENAME, "agents.toml"} or name.endswith("-agents.toml")


def _safe_resolved_path(path: Path) -> Path | None:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _same_file_identity(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except (OSError, ValueError):
        return False


def _agents_config_candidate_paths(target: Path) -> list[Path]:
    from ralph.config._config_filenames import _GLOBAL_AGENTS_FILENAME

    candidates = [
        target.parent / _GLOBAL_AGENTS_FILENAME,
        target.parent / "agents.toml",
    ]
    with suppress(OSError, RuntimeError):
        candidates.append(resolve_global_config_dir() / _GLOBAL_AGENTS_FILENAME)

    with suppress(OSError):
        if target.parent.is_dir():
            for sibling in target.parent.iterdir():
                if _agents_config_filename(sibling.name) and sibling not in candidates:
                    candidates.append(sibling)
    return candidates


def _find_matching_agents_config(path: Path) -> Path | None:
    resolved_path = _safe_resolved_path(path)
    for candidate in _agents_config_candidate_paths(path):
        if candidate == path:
            continue
        if not candidate.exists():
            continue
        resolved_candidate = _safe_resolved_path(candidate)
        if resolved_candidate is None:
            continue
        if resolved_path is not None and resolved_candidate == resolved_path:
            return candidate
        if _same_file_identity(path, candidate):
            return candidate
        if resolved_path is not None and _same_file_identity(resolved_path, candidate):
            return candidate
    return None


def _is_agents_config_path(path: Path) -> bool:
    if _agents_config_filename(path.name):
        return True
    resolved = _safe_resolved_path(path)
    if resolved is not None and _agents_config_filename(resolved.name):
        return True
    return _find_matching_agents_config(path) is not None


def _agents_write_target(path: Path) -> Path:
    if _agents_config_filename(path.name):
        return path
    resolved = _safe_resolved_path(path)
    if resolved is not None and _agents_config_filename(resolved.name):
        return resolved
    matched = _find_matching_agents_config(path)
    if matched is not None:
        return matched
    return path


def _agent_chains_from_toml(text: str) -> dict[str, list[str]]:
    parsed = cast("dict[str, object]", tomllib.loads(text))
    raw_chains = parsed.get("agent_chains")
    if not isinstance(raw_chains, dict):
        return {}
    return {
        name: entries
        for name, entries in cast("dict[str, object]", raw_chains).items()
        if isinstance(entries, list) and all(isinstance(entry, str) for entry in entries)
    }


def autowire_chains_to_detected_agent(
    main_config_path: Path, *, detected: list[str] | None = None
) -> list[str] | Literal["kept-default-agent", "chains-customized"] | None:
    defaults_path = Path(__file__).parents[1] / "policy" / "defaults" / "ralph-workflow.toml"
    text = main_config_path.read_text(encoding="utf-8")
    default_text = defaults_path.read_text(encoding="utf-8")
    default_chains = _agent_chains_from_toml(default_text)
    if _agent_chains_from_toml(text) != default_chains:
        return "chains-customized"

    supports = {support.name: support for support in builtin_supports()}
    default_agents = {
        entry.split("/", 1)[0] for entries in default_chains.values() for entry in entries
    }
    if any(
        support is not None and shutil.which(_binary_for(name, support.cmd)) is not None
        for name in default_agents
        if (support := supports.get(name)) is not None
    ):
        return "kept-default-agent"

    selected = detected if detected is not None else detect_installed_agents()
    if not selected:
        return None
    chain_block = re.compile(r"(?ms)^\[agent_chains\]\n.*?(?=^\[|\Z)")
    match = chain_block.search(text)
    if match is None:
        return None

    def replacement(item: Match[str]) -> str:
        return f'{item.group(1)} = ["{selected[0]}"]'

    rewritten = re.sub(
        r"(?m)^(planning|development|analysis|commit)\s*=\s*\[[^\]]*\]$",
        replacement,
        match.group(),
    )
    new_text = text[: match.start()] + rewritten + text[match.end() :]
    if _is_agents_config_path(main_config_path):
        target = _agents_write_target(main_config_path)
        is_hardlink_alias = (
            target != main_config_path
            and not main_config_path.is_symlink()
            and _same_file_identity(main_config_path, target)
        )
        write_agents_toml(
            target,
            new_text,
            trigger=_AUTOWIRE_TRIGGER,
        )
        if is_hardlink_alias:
            with suppress(OSError):
                main_config_path.unlink()
                os.link(target, main_config_path)
    else:
        _commit_deterministic_config_write(
            main_config_path,
            subject="chore(config): update agent configuration",
            write_fn=lambda: write_text_if_changed(
                DEFAULT_FILE_BACKEND,
                main_config_path,
                new_text,
                encoding="utf-8",
            ),
        )
    return sorted(default_agents)


_ENABLE_TRIGGER = "ralph --init: enable detected agents"


def _agent_block_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"^# @AGENT-BLOCK-START: {re.escape(name)}\n"
        rf"(?P<content>.*?)"
        rf"^# @AGENT-BLOCK-END\n?",
        re.MULTILINE | re.DOTALL,
    )


def _inactive_agent_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"^[ \t]*\[\s*inactive_agents\.(?:{re.escape(name)}|\"{re.escape(name)}\"|'{re.escape(name)}')\s*\]",
        re.MULTILINE,
    )


def _uncomment_block(content: str) -> str:
    return "\n".join(
        line[2:] if line.startswith("# ") else line[1:] if line.startswith("#") else line
        for line in content.splitlines()
    )


def _configured_agent_names(text: str) -> set[str]:
    try:
        parsed = cast("dict[str, object]", tomllib.loads(text))
    except ValueError:
        return set()
    agents = parsed.get("agents")
    if not isinstance(agents, dict):
        return set()
    return {str(name) for name, value in agents.items() if isinstance(value, dict)}


def enable_detected_agents(config_path: Path | None = None) -> list[str]:
    path = config_path or resolve_global_config_dir() / "ralph-workflow-agents.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        msg = (
            f"Cannot enable detected agents: {path} is not valid UTF-8. "
            "Run `ralph --regenerate-config` to restore the bundled agents configuration."
        )
        logger.warning(msg)
        _std_logger.warning(msg)
        return []
    enabled: list[str] = []

    for name in detect_installed_agents():
        inactive_match = _inactive_agent_pattern(name).search(text)
        if inactive_match is not None:
            candidate = (
                text[: inactive_match.start()]
                + re.sub(r"\[\s*inactive_agents\.", "[agents.", inactive_match.group(0), count=1)
                + text[inactive_match.end() :]
            )
            try:
                tomllib.loads(candidate)
            except ValueError as exc:
                conflict = (
                    f"[agents.{name}] is already configured"
                    if name in _configured_agent_names(text)
                    else "it conflicts with the existing configuration"
                )
                msg = (
                    f"Skipping conflicting agent block for {name}: {conflict} ({exc}). "
                    f"The existing configuration is kept unchanged (trigger: {_ENABLE_TRIGGER})."
                )
                logger.warning(msg)
                _std_logger.warning(msg)
                continue
            text = candidate
            enabled.append(name)
            continue

        match = _agent_block_pattern(name).search(text)
        if match is None:
            continue
        content = cast("str", match.group("content"))
        uncommented = _uncomment_block(content)
        try:
            tomllib.loads(uncommented)
        except ValueError as exc:
            msg = f"Skipping invalid agent block for {name}: {exc}"
            logger.warning(msg)
            _std_logger.warning(msg)
            continue
        candidate = text[: match.start()] + uncommented + "\n" + text[match.end() :]
        try:
            tomllib.loads(candidate)
        except ValueError as exc:
            conflict = (
                f"[agents.{name}] is already configured"
                if name in _configured_agent_names(text)
                else "it conflicts with the existing configuration"
            )
            msg = (
                f"Skipping conflicting agent block for {name}: {conflict} ({exc}). "
                f"The existing configuration is kept unchanged (trigger: {_ENABLE_TRIGGER})."
            )
            logger.warning(msg)
            _std_logger.warning(msg)
            continue
        text = candidate
        enabled.append(name)

    if enabled:
        write_agents_toml(
            path,
            text,
            trigger=_ENABLE_TRIGGER,
        )
    return enabled
