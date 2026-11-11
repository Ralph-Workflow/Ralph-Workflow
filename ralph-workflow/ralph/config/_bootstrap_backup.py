"""Backup and migration helpers for config bootstrapping."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.config._bootstrap_types import BootstrapResult
from ralph.config._config_filenames import _GLOBAL_POLICY_FILENAME_MAP
from ralph.config.loader import load_toml
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

if TYPE_CHECKING:
    from types import ModuleType

__all__ = [
    "_backup_path",
    "_get_bundled_defaults_dir",
    "_global_policy_target_name",
    "_migrate_legacy_global_config",
    "_resolve_global_policy_source",
]


def _module_attr_or_none(module: ModuleType, attribute: str) -> object | None:
    namespace = cast("dict[str, object]", module.__dict__)
    return namespace.get(attribute)


def _get_bundled_defaults_dir() -> Path:
    """Return the path to the bundled default policy files.

    Computed lazily to avoid circular import: ralph.policy.loader imports
    ralph.phases which imports ralph.pipeline which imports ralph.config.
    """
    import sys

    module_name = "ralph.policy.loader"
    if module_name in sys.modules:
        loader_mod = sys.modules[module_name]
    else:
        import ralph.policy.loader as loader_mod

    policy_loader_file = _module_attr_or_none(loader_mod, "__file__")
    if not isinstance(policy_loader_file, str):
        raise RuntimeError("Failed to resolve __file__ for ralph.policy.loader module")

    bundled_defaults_dir = Path(policy_loader_file).resolve().parent / "defaults"
    if not bundled_defaults_dir.is_dir():
        raise RuntimeError(
            f"Bundled policy defaults directory does not exist: {bundled_defaults_dir}"
        )
    return bundled_defaults_dir


def _backup_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".bak")


def _global_policy_target_name(local_policy_filename: str) -> str:
    return _GLOBAL_POLICY_FILENAME_MAP.get(local_policy_filename, local_policy_filename)


def _resolve_global_policy_source(
    global_dir: Path, local_policy_filename: str, *, force: bool
) -> Path:
    if force:
        return _get_bundled_defaults_dir() / local_policy_filename

    preferred_global_path = global_dir / _global_policy_target_name(local_policy_filename)
    if preferred_global_path.exists():
        return preferred_global_path

    legacy_global_path = global_dir / local_policy_filename
    if legacy_global_path.exists():
        return _get_bundled_defaults_dir() / local_policy_filename

    return _get_bundled_defaults_dir() / local_policy_filename


def _migrate_legacy_global_config(target: Path) -> BootstrapResult | None:
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        data = cast("Mapping[str, object]", load_toml(target))
    except Exception:
        return None
    raw_drains_obj: object = data.get("agent_drains")
    if not isinstance(raw_drains_obj, Mapping):
        return None
    raw_drains = cast("Mapping[str, object]", raw_drains_obj)

    drains: dict[str, object] = {
        key: value for key, value in raw_drains.items() if isinstance(key, str)
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
    # filesystem-write-ok: migration backup preserves existing user configuration metadata
    shutil.copy2(str(target), str(backup))
    write_text_if_changed(
        DEFAULT_FILE_BACKEND,
        target,
        text[:insert_at] + insert_lines + text[insert_at:],
        encoding="utf-8",
    )
    return BootstrapResult(target, "regenerated", backup)
