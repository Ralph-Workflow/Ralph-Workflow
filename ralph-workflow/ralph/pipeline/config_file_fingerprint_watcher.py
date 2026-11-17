from __future__ import annotations

import hashlib
from pathlib import Path

from ralph.config.loader import _global_agents_config_path, _global_config_path
from ralph.pipeline.config_file_fingerprint import compute_config_file_fingerprint


class ConfigFileFingerprintWatcher:
    def __init__(self, default_path: Path | str | None = None) -> None:
        self._default_path: Path | None = (
            Path(default_path).resolve() if default_path is not None else None
        )
        self._last_fingerprints: dict[Path, str | None] = {}
        self._attempted_fingerprints: dict[Path, set[str | None]] = {}
        if self._default_path is not None:
            self.record_failure(self._default_path)

    def _resolve_path(self, path: Path | str | None) -> Path:
        target = path if path is not None else self._default_path
        if target is None:
            raise ValueError("No path provided and no default path configured")
        return Path(target).resolve()

    def record_failure(
        self,
        path: Path | str | None = None,
        fingerprint: str | None = None,
    ) -> str | None:
        resolved = self._resolve_path(path)
        fp = fingerprint if fingerprint is not None else compute_config_file_fingerprint(resolved)
        self._last_fingerprints[resolved] = fp
        attempted = self._attempted_fingerprints.setdefault(resolved, set())
        attempted.add(fp)
        return fp

    @property
    def tracked_paths(self) -> tuple[Path, ...]:
        return tuple(self._last_fingerprints.keys())

    def get_last_fingerprint(self, path: Path | str | None = None) -> str | None:
        resolved = self._resolve_path(path)
        return self._last_fingerprints.get(resolved)

    def get_current_fingerprint(self, path: Path | str | None = None) -> str | None:
        resolved = self._resolve_path(path)
        return compute_config_file_fingerprint(resolved)

    def should_retry(
        self,
        path: Path | str | None = None,
        *,
        consume: bool = True,
        allow_revisit: bool = False,
    ) -> bool:
        resolved = self._resolve_path(path)
        current = compute_config_file_fingerprint(resolved)

        if resolved not in self._last_fingerprints:
            self.record_failure(resolved, fingerprint=current)
            return False

        last = self._last_fingerprints[resolved]
        if current == last:
            return False

        attempted = self._attempted_fingerprints.setdefault(resolved, set())
        if not allow_revisit and current in attempted:
            return False

        if consume:
            self._last_fingerprints[resolved] = current
            attempted.add(current)

        return True

    def mark_attempt(
        self,
        path: Path | str | None = None,
        fingerprint: str | None = None,
    ) -> None:
        resolved = self._resolve_path(path)
        fp = fingerprint if fingerprint is not None else compute_config_file_fingerprint(resolved)
        self._last_fingerprints[resolved] = fp
        self._attempted_fingerprints.setdefault(resolved, set()).add(fp)

    def reset(self, path: Path | str | None = None) -> None:
        if path is not None:
            resolved = Path(path).resolve()
            self._last_fingerprints.pop(resolved, None)
            self._attempted_fingerprints.pop(resolved, None)
        else:
            self._last_fingerprints.clear()
            self._attempted_fingerprints.clear()
            if self._default_path is not None:
                self.record_failure(self._default_path)


def resolve_config_path_for_workspace(
    root: Path | str,
    config_path: Path | str | None = None,
) -> Path:
    if config_path is not None:
        return Path(config_path).resolve()
    resolved_root = Path(root).resolve()
    for candidate in (
        resolved_root / ".agent" / "ralph-workflow.toml",
        resolved_root / "ralph-workflow.toml",
    ):
        if candidate.is_file():
            return candidate.resolve()
    return (resolved_root / ".agent" / "ralph-workflow.toml").resolve()


def resolve_optional_config_path_for_workspace(
    root: Path | str,
    filename: str,
) -> Path:
    resolved_root = Path(root).resolve()
    for candidate in (
        resolved_root / ".agent" / filename,
        resolved_root / filename,
    ):
        if candidate.is_file():
            return candidate.resolve()
    return (resolved_root / ".agent" / filename).resolve()


def resolve_all_config_sources_for_workspace(
    root: Path | str,
    config_path: Path | str | None = None,
) -> list[tuple[str, Path]]:
    resolved_root = Path(root).resolve()
    sources: list[tuple[str, Path]] = [
        ("primary", resolve_config_path_for_workspace(resolved_root, config_path)),
    ]
    sources.extend(
        (
            f"optional:{opt_name}",
            resolve_optional_config_path_for_workspace(resolved_root, opt_name),
        )
        for opt_name in ("agents.toml", "mcp.toml", "policy.toml")
    )
    sources.append(("global:config", _global_config_path().resolve()))
    sources.append(("global:agents", _global_agents_config_path().resolve()))
    return sources


def resolve_all_config_paths_for_workspace(
    root: Path | str,
    config_path: Path | str | None = None,
) -> list[Path]:
    return [path for _, path in resolve_all_config_sources_for_workspace(root, config_path)]


def compute_config_fingerprint_for_workspace(
    root: Path,
    config_path: Path | str | None = None,
) -> str | None:
    sources = resolve_all_config_sources_for_workspace(root, config_path)
    has_any = False
    hasher = hashlib.sha256()
    for label, path in sources:
        fp = compute_config_file_fingerprint(path)
        if fp is not None:
            has_any = True
        hasher.update(f"{label}:{fp or ''}\n".encode())
    if not has_any:
        return None
    return hasher.hexdigest()


__all__ = [
    "ConfigFileFingerprintWatcher",
    "compute_config_fingerprint_for_workspace",
    "resolve_all_config_paths_for_workspace",
    "resolve_all_config_sources_for_workspace",
    "resolve_config_path_for_workspace",
    "resolve_optional_config_path_for_workspace",
]
