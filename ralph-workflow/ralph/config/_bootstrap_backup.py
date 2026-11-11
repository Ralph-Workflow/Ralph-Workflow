
from __future__ import annotations

import logging
import shutil
import tomllib
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.config._bootstrap_types import BootstrapResult
from ralph.config._config_filenames import (
    _GLOBAL_AGENTS_FILENAME,
    _GLOBAL_POLICY_FILENAME_MAP,
)
from ralph.config._paths import resolve_global_config_dir
from ralph.config.loader import load_toml
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

_std_logger = logging.getLogger(__name__)

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
    candidates = [target.parent / _GLOBAL_AGENTS_FILENAME]
    with suppress(OSError, RuntimeError):
        candidates.append(resolve_global_config_dir() / _GLOBAL_AGENTS_FILENAME)
    return candidates


def _agents_config_alias_reason(target: Path) -> str | None:
    if target.name == _GLOBAL_AGENTS_FILENAME:
        return f"target filename is {_GLOBAL_AGENTS_FILENAME}"
    resolved_target = _safe_resolved_path(target)
    if resolved_target is None:
        return "target path could not be resolved to an identity"
    if resolved_target.name == _GLOBAL_AGENTS_FILENAME:
        return f"resolved target filename is {_GLOBAL_AGENTS_FILENAME}"
    for candidate in _agents_config_candidate_paths(target):
        resolved_candidate = _safe_resolved_path(candidate)
        if resolved_candidate is None:
            continue
        if resolved_candidate != resolved_target and not _same_file_identity(
            resolved_target, candidate
        ):
            continue
        return (
            f"target resolves to the same file as agents configuration {candidate} "
            f"(resolved identity {resolved_candidate})"
        )
    return None


def _extract_legacy_agent_drains(target: Path) -> list[tuple[str, str]]:
    try:
        data = cast("Mapping[str, object]", load_toml(target))
    except Exception:
        return []
    raw_drains_obj: object = data.get("agent_drains")
    if not isinstance(raw_drains_obj, Mapping):
        return []
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
    return missing


def _migrate_legacy_global_config(target: Path) -> BootstrapResult | None:
    alias_reason = _agents_config_alias_reason(target)
    if alias_reason is not None:
        msg = (
            f"Skipping legacy global config migration: target {target} refers to "
            f"agents configuration file {_GLOBAL_AGENTS_FILENAME} ({alias_reason})"
        )
        logger.warning(msg)
        _std_logger.warning(msg)
        return None

    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None

    missing = _extract_legacy_agent_drains(target)
    section_start = text.find("[agent_drains]")
    if not missing or section_start == -1:
        return None

    next_section = text.find("\n[", section_start + len("[agent_drains]"))
    insert_at = len(text) if next_section == -1 else next_section + 1
    insert_lines = "".join(f'{name} = "{chain}"\n' for name, chain in missing)
    if insert_at == len(text) and not text.endswith("\n"):
        insert_lines = "\n" + insert_lines

    candidate = text[:insert_at] + insert_lines + text[insert_at:]
    try:
        tomllib.loads(candidate)
    except ValueError as exc:
        msg = f"Candidate migrated config for {target} is invalid TOML, aborting migration: {exc}"
        logger.warning(msg)
        _std_logger.warning(msg)
        return None

    backup = _backup_path(target)
    if backup.exists():
        backup.unlink()
    shutil.copy2(str(target), str(backup))
    write_text_if_changed(
        DEFAULT_FILE_BACKEND,
        target,
        candidate,
        encoding="utf-8",
    )
    return BootstrapResult(target, "regenerated", backup)
