from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

import shutil

from git import InvalidGitRepositoryError, NoSuchPathError, Repo
from loguru import logger

from ralph.config._agent_internal_globs import (
    _AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB,
    AGENT_INTERNAL_DIR_GLOBS,
    AGENT_INTERNAL_ROOT_BASENAMES,
    AGENT_INTERNAL_TOP_LEVEL_BASENAMES,
)
from ralph.config._bootstrap_backup import (
    _backup_path,
    _get_bundled_defaults_dir,
    _global_policy_target_name,
    _migrate_legacy_global_config,
    _resolve_global_policy_source,
)
from ralph.config._config_filenames import (
    _ADVANCED_LOCAL_POLICY_FILENAMES,
    _GLOBAL_AGENTS_FILENAME,
    _GLOBAL_CONFIG_FILENAME,
    _GLOBAL_MCP_FILENAME,
    _LOCAL_CONFIG_FILENAME,
    _LOCAL_CONFIG_SOURCE,
    _LOCAL_MCP_FILENAME,
    _LOCAL_POLICY_FILENAMES,
)
from ralph.config._default_gitignore_patterns import _DEFAULT_GITIGNORE_PATTERNS
from ralph.config._paths import resolve_global_config_dir as _resolve_global_config_dir
from ralph.config.agents_bootstrap import (
    BootstrapResult,
    _commit_config_write,
    _prepare_config_commit,
    write_agents_with_backup,
)
from ralph.config.unreadable_config_finding import (
    UnreadableConfigFinding,
    collect_unreadable_config_files,
)
from ralph.git.operations import _atomic_append_text, create_commit, stage_files
from ralph.git.scoped_auto_commit import (
    ScopedCommitStatus,
    classify_target_for_commit,
    commit_deterministic_writes,
)

__all__ = [
    "BootstrapResult",
    "Repo",
    "ScopedCommitStatus",
    "UnreadableConfigFinding",
    "auto_seed_default_git_exclude",
    "auto_seed_default_gitignore",
    "classify_target_for_commit",
    "collect_unreadable_config_files",
    "commit_deterministic_writes",
    "create_commit",
    "ensure_global_agents_config",
    "ensure_global_config",
    "ensure_global_mcp_config",
    "ensure_global_policy_configs",
    "ensure_local_configs",
    "ensure_local_main_config",
    "ensure_local_support_configs",
    "regenerate_all",
    "resolve_global_config_dir",
    "stage_files",
]


def resolve_global_config_dir(env: Mapping[str, str] | None = None) -> Path:
    return _resolve_global_config_dir(env)


_DEFAULT_GIT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    *tuple(f".agent/{dir_name}/" for dir_name in sorted(AGENT_INTERNAL_DIR_GLOBS)),
    f".agent/{_AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB}",
    *tuple(f".agent/{name}" for name in sorted(AGENT_INTERNAL_TOP_LEVEL_BASENAMES)),
    *tuple(f"/{name}" for name in sorted(AGENT_INTERNAL_ROOT_BASENAMES)),
)


def ensure_global_config(global_dir: Path | None = None, *, force: bool = False) -> BootstrapResult:
    if global_dir is None:
        global_dir = resolve_global_config_dir()
    target = global_dir / _GLOBAL_CONFIG_FILENAME
    source = _get_bundled_defaults_dir() / _GLOBAL_CONFIG_FILENAME
    result = _copy_with_backup(source, target, force)
    if result.action == "skipped":
        migrated = _migrate_legacy_global_config(target)
        if migrated is not None:
            return BootstrapResult(migrated.path, migrated.action, migrated.backup)
    return result


def ensure_global_agents_config(
    global_dir: Path | None = None,
    *,
    force: bool = False,
    create_if_missing: bool = False,
) -> BootstrapResult:
    if global_dir is None:
        global_dir = resolve_global_config_dir()
    target = global_dir / _GLOBAL_AGENTS_FILENAME
    source = _get_bundled_defaults_dir() / _GLOBAL_AGENTS_FILENAME
    if not target.exists() and not (force or create_if_missing):
        return BootstrapResult(target, "skipped", None)
    return write_agents_with_backup(
        source,
        target,
        force,
        trigger="ralph --regenerate-config" if force else "ralph --init",
    )


def ensure_global_mcp_config(
    global_dir: Path | None = None, *, force: bool = False
) -> BootstrapResult:
    if global_dir is None:
        global_dir = resolve_global_config_dir()
    target = global_dir / _GLOBAL_MCP_FILENAME
    source = _get_bundled_defaults_dir() / "mcp.toml"
    return _copy_with_backup(source, target, force)


def ensure_global_policy_configs(
    global_dir: Path | None = None, *, force: bool = False
) -> list[BootstrapResult]:
    if global_dir is None:
        global_dir = resolve_global_config_dir()
    return [
        _copy_with_backup(
            _resolve_global_policy_source(global_dir, policy_filename, force=force),
            global_dir / _global_policy_target_name(policy_filename),
            force,
        )
        for policy_filename in _LOCAL_POLICY_FILENAMES
    ]


def ensure_local_main_config(agent_dir: Path, *, force: bool = False) -> BootstrapResult:
    agent_dir.mkdir(parents=True, exist_ok=True)
    global_source = resolve_global_config_dir() / _GLOBAL_CONFIG_FILENAME
    source = (
        global_source
        if global_source.exists()
        else _get_bundled_defaults_dir() / _LOCAL_CONFIG_SOURCE
    )
    return _copy_with_backup(
        source,
        agent_dir / _LOCAL_CONFIG_FILENAME,
        force,
    )


def ensure_local_support_configs(agent_dir: Path, *, force: bool = False) -> list[BootstrapResult]:
    agent_dir.mkdir(parents=True, exist_ok=True)
    global_dir = resolve_global_config_dir()
    global_mcp_source = global_dir / _GLOBAL_MCP_FILENAME
    mcp_source = (
        global_mcp_source
        if global_mcp_source.exists()
        else _get_bundled_defaults_dir() / "mcp.toml"
    )
    results: list[BootstrapResult] = [
        _copy_with_backup(
            mcp_source,
            agent_dir / _LOCAL_MCP_FILENAME,
            force,
        )
    ]
    results.extend(
        _copy_with_backup(
            _resolve_global_policy_source(global_dir, policy_filename, force=False),
            agent_dir / policy_filename,
            force,
        )
        for policy_filename in _LOCAL_POLICY_FILENAMES
    )
    _ensure_default_gitignore(agent_dir.parent)
    _ensure_default_git_exclude(agent_dir.parent)
    return results


def ensure_local_configs(agent_dir: Path, *, force: bool = False) -> list[BootstrapResult]:
    return [
        ensure_local_main_config(agent_dir, force=force),
        *ensure_local_support_configs(agent_dir, force=force),
    ]


def _ensure_default_gitignore(repo_root: Path) -> None:
    auto_seed_default_gitignore(repo_root)


def _ensure_default_git_exclude(repo_root: Path) -> None:
    from git import InvalidGitRepositoryError

    from ralph.git.commit_review import add_to_git_exclude

    try:
        add_to_git_exclude(repo_root, list(_DEFAULT_GIT_EXCLUDE_PATTERNS))
    except InvalidGitRepositoryError:
        return


def _resolve_git_exclude_path(repo_root: Path) -> Path | None:
    try:
        repo = Repo(repo_root, search_parent_directories=False)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return None
    try:
        git_dir = Path(repo.git_dir)
    finally:
        repo.close()
    return git_dir / "info" / "exclude"


def auto_seed_default_git_exclude(repo_root: Path) -> list[str]:
    exclude_path = _resolve_git_exclude_path(repo_root)
    if exclude_path is None:
        exclude_path = repo_root / ".git" / "info" / "exclude"
    existing: set[str] = set()
    file_existed = exclude_path.exists()
    if file_existed:
        try:
            existing = set(exclude_path.read_text(encoding="utf-8").splitlines())
        except OSError as exc:
            logger.warning(
                "Could not read existing git exclude at {}: {}. "
                "Proceeding as if the file were empty; this may append "
                "duplicate patterns on a later run. Check file permissions "
                "and ownership to remove the warning.",
                exclude_path,
                exc,
            )
            existing = set()
    missing = [p for p in _DEFAULT_GIT_EXCLUDE_PATTERNS if p not in existing]
    if missing:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(missing) + "\n"
        _atomic_append_text(exclude_path, payload)
    return list(missing)


def auto_seed_default_gitignore(repo_root: Path) -> list[str]:
    gitignore_path = repo_root / ".gitignore"
    existing: set[str] = set()
    if gitignore_path.exists():
        try:
            existing = set(gitignore_path.read_text(encoding="utf-8").splitlines())
        except OSError as exc:
            logger.warning(
                "Could not read existing .gitignore at {}: {}. "
                "Proceeding as if the file were empty; this may append "
                "duplicate patterns on a later run. Check file permissions "
                "and ownership to remove the warning.",
                gitignore_path,
                exc,
            )
            existing = set()
    missing = [p for p in _DEFAULT_GITIGNORE_PATTERNS if p not in existing]
    appended: list[str] = []
    if missing:
        from ralph.git.operations import (
            create_commit,
        )
        from ralph.git.scoped_auto_commit import (
            ScopedCommitStatus,
            capture_pre_write_contents,
            commit_deterministic_writes,
        )

        pre_contents = capture_pre_write_contents(repo_root, [".gitignore"])
        gitignore_path = repo_root / ".gitignore"
        payload = "\n".join(missing) + "\n"
        _atomic_append_text(gitignore_path, payload)
        appended = list(missing)
        try:
            from ralph.git.operations import stage_files as _stage_files

            result = commit_deterministic_writes(
                repo_root,
                paths=[".gitignore"],
                pre_contents=pre_contents,
                subject="chore(gitignore): seed ralph defaults",
                create_commit_fn=create_commit,
                stage_fn=_stage_files,
            )
            if result.status is ScopedCommitStatus.CREATED and result.sha:
                logger.debug(".gitignore auto-seed committed: {}", result.sha[:8])
            elif result.status is ScopedCommitStatus.SKIPPED and result.skipped_paths:
                logger.warning(
                    ".gitignore auto-seed skipped: .gitignore was already dirty at HEAD "
                    "(user mid-edit?); left for the user flow"
                )
            elif result.status is ScopedCommitStatus.FAILED:
                logger.warning(".gitignore auto-seed commit failed (non-fatal): {}", result.error)
        except Exception as exc:
            logger.warning(".gitignore auto-seed commit failed (non-fatal): {}", exc)
    return appended


def _regenerate_existing_local_configs(
    agent_dir: Path, *, global_dir: Path
) -> list[BootstrapResult]:
    sources = {
        _LOCAL_CONFIG_FILENAME: _get_bundled_defaults_dir() / _LOCAL_CONFIG_SOURCE,
        _LOCAL_MCP_FILENAME: _get_bundled_defaults_dir() / "mcp.toml",
        **{
            policy_filename: _resolve_global_policy_source(global_dir, policy_filename, force=False)
            for policy_filename in _LOCAL_POLICY_FILENAMES
        },
        **{
            policy_filename: _get_bundled_defaults_dir() / policy_filename
            for policy_filename in _ADVANCED_LOCAL_POLICY_FILENAMES
        },
    }
    return [
        write_agents_with_backup(source, target, True, trigger="ralph --regenerate-config")
        if filename in _ADVANCED_LOCAL_POLICY_FILENAMES
        else _copy_with_backup(source, target, True)
        for filename, source in sources.items()
        if (target := agent_dir / filename).exists()
    ]


def regenerate_all(
    *,
    global_dir: Path | None = None,
    agent_dir: Path | None = None,
) -> list[BootstrapResult]:
    resolved_global_dir = global_dir or resolve_global_config_dir()
    results: list[BootstrapResult] = [
        ensure_global_config(resolved_global_dir, force=True),
        ensure_global_agents_config(resolved_global_dir, force=True),
        ensure_global_mcp_config(resolved_global_dir, force=True),
        *ensure_global_policy_configs(resolved_global_dir, force=True),
    ]
    if agent_dir is not None:
        results.extend(
            _regenerate_existing_local_configs(agent_dir, global_dir=resolved_global_dir)
        )
    return results


def _copy_with_backup(source: Path, target: Path, force: bool) -> BootstrapResult:
    target.parent.mkdir(parents=True, exist_ok=True)
    pre_existed = target.exists()

    if pre_existed and not force:
        return BootstrapResult(target, "skipped", None)

    plan = _prepare_config_commit(target)

    backup: Path | None = None
    if pre_existed and force:
        backup = _backup_path(target)
        if backup.exists():
            backup.unlink()
        shutil.move(str(target), str(backup))

    shutil.copy2(str(source), str(target))
    action: Literal["created", "skipped", "regenerated"] = (
        "regenerated" if pre_existed else "created"
    )
    _commit_config_write(target, plan)
    return BootstrapResult(target, action, backup)
