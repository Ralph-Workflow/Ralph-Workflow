"""Enable bundled agent configuration blocks for CLIs found on PATH."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tomllib
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
    """Wrap a project-local config write with the deterministic auto-commit contract.

    This is the shared producer-side wiring the Ralph-owned config write
    sites that AUTO-COMMIT their change route through after wt-012
    (the ``ralph --init`` chain autowire
    ``autowire_chains_to_detected_agent`` and the ``ralph --init``
    starter-prompt creation). Write sites that publish
    WITHOUT a commit do not route through this helper: ``enable_detected_agents``
    publishes the agents file through ``write_agents_toml`` (atomic replace plus
    ``.bak`` backup) and deliberately creates no chore commit. Read paths
    are not write sites at all: ``load_toml`` migrates the retired
    ``agent_can_commit`` keys in memory only, so it never writes and
    never routes through this helper. The contract
    mirrors the skill / policy ``commit_deterministic_writes`` callers:

    * the pre-write content hash of ``config_path`` is captured BEFORE
      ``write_fn`` runs, so the deterministic commit can isolate the
      change from any user / agent edit that races the call;
    * ``write_fn`` is always invoked -- the write must land on disk
      even when the path is outside a git repo, so a non-repo config
      write is the documented silent-NOOP contract (a clean write,
      no commit, no error);
    * ``commit_deterministic_writes`` is only called when the path
      is inside a git working tree; the production
      ``create_commit`` / ``stage_files`` are the default wiring and
      tests inject stubs via ``create_commit_fn`` / ``stage_fn``;
    * the ``ScopedCommitResult`` is logged at the documented level
      per status (``DEBUG`` for ``CREATED`` / ``NOOP`` / ``NOT_REPO``,
      ``WARNING`` for ``SKIPPED`` to name the path that was already
      dirty at HEAD, ``ERROR`` for ``FAILED`` to surface
      ``result.error`` to the operator). The helper never raises on
      a FAILED commit -- a broken git state must not block the
      pipeline; the failure is visible to the operator via the
      log line.

    Args:
        config_path: The file the config write touches. Absolute or
            cwd-relative; both are resolved before the repo-relative
            path is computed.
        subject: The conventional-commit subject line for the
            deterministic chore commit. Pinned per site.
        write_fn: The zero-arg callable that performs the actual write
            (typically a closure that calls ``write_text_if_changed``
            or ``atomic_write_text_if_changed`` with the right
            arguments). It is called exactly once, AFTER the pre-write
            content hash is captured.
        create_commit_fn: Optional override for the production
            ``create_commit`` (used by tests that inject a
            failing / recording stub).
        stage_fn: Optional override for the production
            ``stage_files`` (used by tests).

    Returns:
        The :class:`ScopedCommitResult` on every path that called
        ``commit_deterministic_writes``; ``None`` when the path is
        outside a git working tree (silent NOOP).
    """
    cc = create_commit_fn if create_commit_fn is not None else create_commit
    sf = stage_fn if stage_fn is not None else stage_files
    # When ``config_path`` does not exist yet (a fresh starter file on
    # first ``ralph --init``), ``Repo(path)`` raises ``NoSuchPathError``
    # even with ``search_parent_directories=True``. Fall back to the
    # parent directory so the producer can locate the owning repo via
    # the parent search rather than the (non-existent) file path. If
    # the parent itself is not in a repo, ``find_repo_root`` raises and
    # the documented silent-NOOP branch below runs.
    repo_root_search_anchor = config_path if config_path.exists() else config_path.parent
    try:
        repo_root = find_repo_root(repo_root_search_anchor)
    except (GitOperationError, OSError) as exc:
        # GitOperationError is the documented ``find_repo_root`` failure
        # when neither ``config_path`` nor its parent is inside a git
        # working tree. OSError is the secondary guard for broken
        # filesystem access. Both collapse to the documented silent-NOOP
        # contract: write the file, skip the commit, do not raise.
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
    # wt-012 DA-001/DA-008/DA-011: when ``config_path`` is a symlink, the
    # actual file the write mutates depends on the backend primitive:
    # a ``Path.replace`` swaps the symlink for a regular file, so the
    # change lands at the LEXICAL ``rel_path``; ``write_text_if_changed``
    # (autowire) opens the path with
    # ``path.open("w")`` which FOLLOWS the symlink and writes through
    # to the RESOLVED target. We cannot tell at compile time which
    # primitive the caller's ``write_fn`` uses, so capture BOTH the
    # lexical and the resolved paths in ``pre_contents`` and pass them
    # to the deterministic commit helper. The helper dedupes and
    # stages whichever path actually changed -- the lexical for the
    # atomic case (replacement), the resolved for the through-link
    # case (target rewrite). The single-path writers
    # (non-symlinked config_path) capture only the lexical path as
    # before; the extra entry is no-op.
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
    """Surface the ``commit_deterministic_writes`` outcome at the documented level.

    ``CREATED`` / ``NOOP`` / ``NOT_REPO``: DEBUG (success / expected
    silent skip).
    ``SKIPPED``: WARNING with the path that was already dirty at HEAD
    so an operator can attribute the file to the agent flow that
    dirtied it.
    ``FAILED``: ERROR with ``result.error`` and the path list, but
    never raises -- a broken git state must not block the pipeline.
    """
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
    """Return the PATH binary for a built-in command, honoring documented overrides."""
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
    """Return built-in agent names whose command binary is available on PATH."""
    return [
        support.name
        for support in builtin_supports()
        if shutil.which(_binary_for(support.name, support.cmd)) is not None
    ]


#: Trigger label of the ``autowire_chains_to_detected_agent`` write on agents targets.
_AUTOWIRE_TRIGGER = "ralph --init: autowire agent chains"


def _is_agents_config_path(path: Path) -> bool:
    """Return True if *path* names an agents configuration file."""
    from ralph.config._config_filenames import _GLOBAL_AGENTS_FILENAME

    return path.name in {_GLOBAL_AGENTS_FILENAME, "agents.toml"} or path.name.endswith(
        "-agents.toml"
    )


def _agent_chains_from_toml(text: str) -> dict[str, list[str]]:
    """Read the flat chain mapping from a bundled or user main config."""
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
    """Point untouched default chains at a detected CLI when Claude is unavailable."""
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
        write_agents_toml(main_config_path, new_text, trigger=_AUTOWIRE_TRIGGER)
    else:
        # wt-012: route the project-local config write through the shared
        # deterministic auto-commit primitive so a chore commit with the
        # fixed ``chore(config): update agent configuration`` subject
        # captures the chain rewrite, never leaking it into a later
        # agent's commit. A path outside a git repo (e.g. a global
        # ``~/.config/`` config) is the documented silent-NOOP path --
        # the write still lands on disk so the operator's config is
        # always current, and no commit is attempted.
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


#: Trigger label of the ``enable_detected_agents`` write. The same label
#: names the trigger in the warning that reports a skipped block, so the
#: operator can attribute the skip to the flow that asked for it.
_ENABLE_TRIGGER = "ralph --init: enable detected agents"


def _agent_block_pattern(name: str) -> re.Pattern[str]:
    """Return the pattern that matches the commented ``@AGENT-BLOCK`` of *name*."""
    return re.compile(
        rf"^# @AGENT-BLOCK-START: {re.escape(name)}\n"
        rf"(?P<content>.*?)"
        rf"^# @AGENT-BLOCK-END\n?",
        re.MULTILINE | re.DOTALL,
    )


def _uncomment_block(content: str) -> str:
    """Return the block body with its leading comment markers removed."""
    return "\n".join(
        line[2:] if line.startswith("# ") else line[1:] if line.startswith("#") else line
        for line in content.splitlines()
    )


def _configured_agent_names(text: str) -> set[str]:
    """Return the agent names the configuration in *text* already defines.

    A document that does not parse defines nothing: the caller then reports
    the parse failure of the candidate document instead of guessing which
    agent is already configured.
    """
    try:
        parsed = cast("dict[str, object]", tomllib.loads(text))
    except ValueError:
        return set()
    agents = parsed.get("agents")
    if not isinstance(agents, dict):
        return set()
    return {str(name) for name, value in agents.items() if isinstance(value, dict)}


def enable_detected_agents(config_path: Path | None = None) -> list[str]:
    """Activate untouched bundled blocks for installed agents, without changing active ones.

    Every detected agent is activated one block at a time. The block is
    uncommented, validated on its own, and the WHOLE candidate document --
    the current file with that one block enabled -- is validated with
    ``tomllib`` before it is accepted. A block that conflicts with what the
    file already carries (an ``[agents.<name>]`` table the operator
    configured, for instance) would make the whole document invalid, so it
    is skipped with a warning that names the agent and the trigger, while
    every other block is still applied. The operator's own lines are never
    rewritten: a skipped block stays commented and the rest of the file is
    left byte-identical.

    The file is written once, at the end, and only when at least one block
    was applied.

    Args:
        config_path: The agents configuration to activate blocks in. Defaults
            to ``<global config dir>/ralph-workflow-agents.toml``.

    Returns:
        The names of the agents whose block was activated, in detection order.
    """
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
        match = _agent_block_pattern(name).search(text)
        if match is None:
            continue
        content = cast(
            "str", match.group("content")
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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
