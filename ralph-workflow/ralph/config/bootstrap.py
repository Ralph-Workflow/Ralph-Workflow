"""Bootstrap helpers for creating user-global and project-local config files.

Auto-creates ~/.config/ralph-workflow.toml and ~/.config/ralph-workflow-mcp.toml
from the bundled, fully-commented templates on first run. Also supports
regenerating configs with .bak backups via --regenerate-config.

Bootstrap creates the standard first-run config set:
  - User-global: ~/.config/ralph-workflow.toml, ~/.config/ralph-workflow-mcp.toml
  - Project-local: .agent/ralph-workflow.toml, .agent/mcp.toml,
                   .agent/pipeline.toml, .agent/artifacts.toml
  - Advanced optional: .agent/agents.toml (only regenerated when already present)
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

_GLOBAL_CONFIG_FILENAME = "ralph-workflow.toml"
_GLOBAL_MCP_FILENAME = "ralph-workflow-mcp.toml"
_LOCAL_CONFIG_FILENAME = "ralph-workflow.toml"
_LOCAL_MCP_FILENAME = "mcp.toml"
_LOCAL_POLICY_FILENAMES = ("pipeline.toml", "artifacts.toml")
_ADVANCED_LOCAL_POLICY_FILENAMES = ("agents.toml",)
_LOCAL_CONFIG_SOURCE = "ralph-workflow-local.toml"


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
    return _copy_with_backup(source, target, force)


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


def ensure_local_main_config(agent_dir: Path, *, force: bool = False) -> BootstrapResult:
    """Ensure the project-local main override exists.

    Args:
        agent_dir: The .agent directory to write configs into.
        force: When True, overwrite an existing file (backing it up first).

    Returns:
        BootstrapResult describing the action taken for `.agent/ralph-workflow.toml`.
    """
    agent_dir.mkdir(parents=True, exist_ok=True)
    return _copy_with_backup(
        _get_bundled_defaults_dir() / _LOCAL_CONFIG_SOURCE,
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
    results: list[BootstrapResult] = [
        _copy_with_backup(
            _get_bundled_defaults_dir() / "mcp.toml",
            agent_dir / _LOCAL_MCP_FILENAME,
            force,
        )
    ]
    results.extend(
        _copy_with_backup(
            _get_bundled_defaults_dir() / policy_filename,
            agent_dir / policy_filename,
            force,
        )
        for policy_filename in _LOCAL_POLICY_FILENAMES
    )
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
    ]
    if agent_dir is not None:
        results.extend(ensure_local_configs(agent_dir, force=True))
        results.extend(_regenerate_existing_advanced_local_configs(agent_dir))
    return results


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
        backup = target.with_suffix(target.suffix + ".bak")
        if backup.exists():
            backup.unlink()
        shutil.move(str(target), str(backup))

    shutil.copy2(str(source), str(target))
    action: Literal["created", "skipped", "regenerated"] = (
        "regenerated" if pre_existed else "created"
    )
    return BootstrapResult(target, action, backup)
