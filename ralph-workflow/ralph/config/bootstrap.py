"""Bootstrap helpers for creating user-global and project-local config files.

Auto-creates the user-global Ralph config set on first run, including
~/.config/ralph-workflow.toml, ~/.config/ralph-workflow-mcp.toml,
~/.config/pipeline.toml, and ~/.config/artifacts.toml from bundled templates.
Also supports regenerating configs with .bak backups via --regenerate-config.

Bootstrap creates the standard first-run config set:
  - User-global: ~/.config/ralph-workflow.toml, ~/.config/ralph-workflow-mcp.toml,
                 ~/.config/pipeline.toml, ~/.config/artifacts.toml
  - Project-local: .agent/ralph-workflow.toml, .agent/mcp.toml,
                   .agent/pipeline.toml, .agent/artifacts.toml
  - Advanced optional: .agent/agents.toml (only regenerated when already present)
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from ralph.git.operations import append_to_gitignore

_GLOBAL_CONFIG_FILENAME = "ralph-workflow.toml"
_GLOBAL_MCP_FILENAME = "ralph-workflow-mcp.toml"
_LOCAL_CONFIG_FILENAME = "ralph-workflow.toml"
_LOCAL_MCP_FILENAME = "mcp.toml"
_LOCAL_POLICY_FILENAMES = ("pipeline.toml", "artifacts.toml")
_GLOBAL_POLICY_FILENAMES = _LOCAL_POLICY_FILENAMES
_ADVANCED_LOCAL_POLICY_FILENAMES = ("agents.toml",)
_LOCAL_CONFIG_SOURCE = "ralph-workflow-local.toml"
_DEFAULT_GITIGNORE_PATTERNS = (".agent/", "/PROMPT*", "wt-*/")


def _get_bundled_defaults_dir() -> Path:
    """Return the path to the bundled default policy files.

    Computed lazily to avoid circular import: ralph.policy.loader imports
    ralph.phases which imports ralph.pipeline which imports ralph.config.
    """
    import ralph.policy  # noqa: PLC0415

    return Path(ralph.policy.__file__).parent / "defaults"


@dataclass(frozen=True)
class BootstrapResult:
    """Result of a bootstrap operation.

    Attributes:
        path: Target file path that was acted on.
        action: What happened: created, skipped, or regenerated.
        backup: Path to the .bak file if the original was backed up, else None.
    """

    path: Path
    action: Literal["created", "skipped", "regenerated"]
    backup: Path | None = None


def resolve_global_config_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the user-global config directory.

    Honors XDG_CONFIG_HOME when set; falls back to ~/.config.

    Args:
        env: Environment mapping to read from. Uses os.environ when None.

    Returns:
        Path to the config directory.
    """
    env_map: Mapping[str, str] = os.environ if env is None else env
    xdg = env_map.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"


def ensure_global_config(global_dir: Path | None = None, *, force: bool = False) -> BootstrapResult:
    """Ensure ~/.config/ralph-workflow.toml exists, creating it from the bundled template.

    Args:
        global_dir: Override the global config directory. Defaults to resolve_global_config_dir().
        force: When True, overwrite an existing file (backs it up to <name>.bak first).

    Returns:
        BootstrapResult describing the action taken.
    """
    if global_dir is None:
        global_dir = resolve_global_config_dir()
    target = global_dir / _GLOBAL_CONFIG_FILENAME
    source = _get_bundled_defaults_dir() / _GLOBAL_CONFIG_FILENAME
    result = _copy_with_backup(source, target, force)
    if result.action == "skipped":
        migrated = _migrate_legacy_global_config(target)
        if migrated is not None:
            return migrated
    return result


def ensure_global_mcp_config(
    global_dir: Path | None = None, *, force: bool = False
) -> BootstrapResult:
    """Ensure ~/.config/ralph-workflow-mcp.toml exists, creating it from the bundled template.

    Args:
        global_dir: Override the global config directory. Defaults to resolve_global_config_dir().
        force: When True, overwrite an existing file (backs it up to <name>.bak first).

    Returns:
        BootstrapResult describing the action taken.
    """
    if global_dir is None:
        global_dir = resolve_global_config_dir()
    target = global_dir / _GLOBAL_MCP_FILENAME
    source = _get_bundled_defaults_dir() / "mcp.toml"
    return _copy_with_backup(source, target, force)


def ensure_global_policy_configs(
    global_dir: Path | None = None, *, force: bool = False
) -> list[BootstrapResult]:
    """Ensure the user-global policy defaults exist.

    Args:
        global_dir: Override the global config directory. Defaults to resolve_global_config_dir().
        force: When True, overwrite existing files (backs them up first).

    Returns:
        List of BootstrapResult, one per global policy file.
    """
    if global_dir is None:
        global_dir = resolve_global_config_dir()
    return [
        _copy_with_backup(
            _get_bundled_defaults_dir() / policy_filename,
            global_dir / policy_filename,
            force,
        )
        for policy_filename in _GLOBAL_POLICY_FILENAMES
    ]


def ensure_local_main_config(agent_dir: Path, *, force: bool = False) -> BootstrapResult:
    """Ensure the project-local main override exists.

    Args:
        agent_dir: The .agent directory to write configs into.
        force: When True, overwrite an existing file (backing it up first).

    Returns:
        BootstrapResult describing the action taken for `.agent/ralph-workflow.toml`.
    """
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
    """Ensure the standard project-local policy and MCP files exist.

    This scaffolds the `.agent/` files Ralph needs for project-local runtime behavior
    without creating the optional project-local main override.

    Args:
        agent_dir: The .agent directory to write configs into.
        force: When True, overwrite existing files (backs them up first).

    Returns:
        List of BootstrapResult, one per support file.
    """
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
            (global_dir / policy_filename)
            if (global_dir / policy_filename).exists()
            else _get_bundled_defaults_dir() / policy_filename,
            agent_dir / policy_filename,
            force,
        )
        for policy_filename in _LOCAL_POLICY_FILENAMES
    )
    _ensure_default_gitignore(agent_dir.parent)
    return results



def ensure_local_configs(agent_dir: Path, *, force: bool = False) -> list[BootstrapResult]:
    """Ensure the full project-local config set exists.

    Args:
        agent_dir: The .agent directory to write configs into.
        force: When True, overwrite existing files (backs them up first).

    Returns:
        List of BootstrapResult, one per config file.
    """
    return [
        ensure_local_main_config(agent_dir, force=force),
        *ensure_local_support_configs(agent_dir, force=force),
    ]


def _ensure_default_gitignore(repo_root: Path) -> None:
    append_to_gitignore(repo_root, list(_DEFAULT_GITIGNORE_PATTERNS))


def _regenerate_existing_advanced_local_configs(agent_dir: Path) -> list[BootstrapResult]:
    """Regenerate advanced local configs only when they already exist."""
    results: list[BootstrapResult] = []
    for policy_filename in _ADVANCED_LOCAL_POLICY_FILENAMES:
        target = agent_dir / policy_filename
        if target.exists():
            results.append(
                _copy_with_backup(
                    _get_bundled_defaults_dir() / policy_filename,
                    target,
                    True,
                )
            )
    return results


def regenerate_all(
    *,
    global_dir: Path | None = None,
    agent_dir: Path | None = None,
) -> list[BootstrapResult]:
    """Regenerate all configs from bundled defaults, backing up existing files.

    Args:
        global_dir: Override the global config directory. Defaults to resolve_global_config_dir().
        agent_dir: The .agent directory to regenerate local configs in. Skipped when None.

    Returns:
        Flat list of BootstrapResult for every file touched.
    """
    results: list[BootstrapResult] = [
        ensure_global_config(global_dir, force=True),
        ensure_global_mcp_config(global_dir, force=True),
        *ensure_global_policy_configs(global_dir, force=True),
    ]
    if agent_dir is not None:
        results.extend(ensure_local_configs(agent_dir, force=True))
        results.extend(_regenerate_existing_advanced_local_configs(agent_dir))
    return results


def _backup_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".bak")



def _migrate_legacy_global_config(target: Path) -> BootstrapResult | None:
    from ralph.config.loader import load_toml  # noqa: PLC0415

    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None

    data = cast("Mapping[str, object]", load_toml(target))
    raw_drains_obj: object = data.get("agent_drains")
    if not isinstance(raw_drains_obj, Mapping):
        return None
    raw_drains = cast("Mapping[str, object]", raw_drains_obj)

    drains: dict[str, object] = {
        key: value
        for key, value in raw_drains.items()
        if isinstance(key, str)
    }
    missing: list[tuple[str, str]] = []
    analysis_chain: object = drains.get("analysis")
    commit_chain: object = drains.get("commit")
    if isinstance(analysis_chain, str):
        missing.extend(
            (drain_name, analysis_chain)
            for drain_name in ("planning_analysis", "development_analysis")
            if drain_name not in drains
        )
    if isinstance(commit_chain, str) and "development_commit" not in drains:
        missing.append(("development_commit", commit_chain))

    section_start = text.find("[agent_drains]")
    if not missing or section_start == -1:
        return None

    next_section = text.find("\n[", section_start + len("[agent_drains]"))
    insert_at = len(text) if next_section == -1 else next_section + 1
    insert_lines = "".join(f'{name} = "{chain}"\n' for name, chain in missing)
    if insert_at == len(text) and not text.endswith("\n"):
        insert_lines = "\n" + insert_lines

    backup = _backup_path(target)
    if backup.exists():
        backup.unlink()
    shutil.copy2(str(target), str(backup))
    target.write_text(text[:insert_at] + insert_lines + text[insert_at:], encoding="utf-8")
    return BootstrapResult(target, "regenerated", backup)



def _copy_with_backup(source: Path, target: Path, force: bool) -> BootstrapResult:
    """Copy source to target, optionally backing up an existing target first.

    The backup (.bak) is always in the same directory as target, so a
    cross-device move is impossible and shutil.move is safe.

    Args:
        source: Bundled template to copy.
        target: Destination path.
        force: When True, overwrite target (with backup). When False, skip if target exists.

    Returns:
        BootstrapResult describing what happened.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    pre_existed = target.exists()

    if pre_existed and not force:
        return BootstrapResult(target, "skipped", None)

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
    return BootstrapResult(target, action, backup)
