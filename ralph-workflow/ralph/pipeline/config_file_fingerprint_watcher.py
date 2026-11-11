"""Stateful watcher over configuration-file fingerprints.

The watcher tracks the fingerprints of failing configuration files so the
self-recovery path retries once per distinct fingerprint: after a failing
file is repaired (its fingerprint changes) a retry is allowed, and an
unchanged file never triggers another retry, which prevents endless spin.

The pure fingerprint helpers it consumes live in
:mod:`ralph.pipeline.config_file_fingerprint`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ralph.config.loader import _global_agents_config_path, _global_config_path
from ralph.pipeline.config_file_fingerprint import compute_config_file_fingerprint


class ConfigFileFingerprintWatcher:
    """Stateful watcher tracking config file fingerprints for self-recovery retries.

    Ensures that after a failing configuration file is repaired (fingerprint changes),
    retry occurs once per distinct fingerprint, preventing endless spin when unchanged.
    """

    def __init__(self, default_path: Path | str | None = None) -> None:
        self._default_path: Path | None = (
            Path(default_path).resolve() if default_path is not None else None
        )
        self._last_fingerprints: dict[
            Path, str | None
        ] = {}  # bounded-accumulator-ok: one entry per tracked config path
        self._attempted_fingerprints: dict[
            Path, set[str | None]
        ] = {}  # bounded-accumulator-ok: one entry per tracked config path
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
        """Record the initial failure state for a configuration file.

        Args:
            path: Target file path. Uses default_path if None.
            fingerprint: Pre-computed fingerprint, or None to compute from disk.

        Returns:
            The recorded failure fingerprint.
        """
        resolved = self._resolve_path(path)
        fp = fingerprint if fingerprint is not None else compute_config_file_fingerprint(resolved)
        self._last_fingerprints[resolved] = fp
        attempted = self._attempted_fingerprints.setdefault(resolved, set())
        attempted.add(fp)
        return fp

    def get_last_fingerprint(self, path: Path | str | None = None) -> str | None:
        """Return the last recorded or attempted fingerprint for the given path."""
        resolved = self._resolve_path(path)
        return self._last_fingerprints.get(resolved)

    def get_current_fingerprint(self, path: Path | str | None = None) -> str | None:
        """Compute the current fingerprint on disk for the given path."""
        resolved = self._resolve_path(path)
        return compute_config_file_fingerprint(resolved)

    def should_retry(
        self,
        path: Path | str | None = None,
        *,
        consume: bool = True,
        allow_revisit: bool = False,
    ) -> bool:
        """Determine whether retry is appropriate for the file.

        Returns True if the file's fingerprint has changed since the last failure
        or attempt and has not already been retried (unless allow_revisit=True).

        If consume=True (the default), marks the new fingerprint as attempted
        so subsequent checks with unchanged content return False, preventing endless spin.
        """
        resolved = self._resolve_path(path)
        current = compute_config_file_fingerprint(resolved)

        if resolved not in self._last_fingerprints:
            # First observation of this path: record baseline failure state and wait for repair
            self.record_failure(resolved, fingerprint=current)
            return False

        last = self._last_fingerprints[resolved]
        if current == last:
            # Unchanged: do not retry
            return False

        attempted = self._attempted_fingerprints.setdefault(resolved, set())
        if not allow_revisit and current in attempted:
            # Already retried for this distinct fingerprint: do not retry again
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
        """Mark a fingerprint as attempted without checking should_retry."""
        resolved = self._resolve_path(path)
        fp = fingerprint if fingerprint is not None else compute_config_file_fingerprint(resolved)
        self._last_fingerprints[resolved] = fp
        self._attempted_fingerprints.setdefault(resolved, set()).add(fp)

    def reset(self, path: Path | str | None = None) -> None:
        """Reset state for a specific path, or all paths if path is None."""
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
    """Return the configuration file path for a workspace root.

    Checks ``config_path`` if given, then ``root / '.agent' / 'ralph-workflow.toml'``,
    and falls back to ``root / 'ralph-workflow.toml'``.
    """
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
    """Return the resolved path for an optional configuration file in a workspace.

    Checks ``root / '.agent' / filename`` first, then ``root / filename``
    if it exists, falling back to ``root / '.agent' / filename``.
    """
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
    """Return all relevant loaded repairable configuration sources for a workspace.

    Collects:
      1. Primary config: explicit config_path, or .agent/ralph-workflow.toml / ralph-workflow.toml
      2. Optional configs: .agent/agents.toml, .agent/mcp.toml, .agent/policy.toml, or root equivalents
      3. Global configs: _global_config_path(), _global_agents_config_path()

    Returns:
        List of ``(label, path)`` tuples identifying each configuration source.
    """
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
    """Return all relevant loaded repairable configuration file paths for a workspace."""
    return [path for _, path in resolve_all_config_sources_for_workspace(root, config_path)]


def compute_config_fingerprint_for_workspace(
    root: Path,
    config_path: Path | str | None = None,
) -> str | None:
    """Compute the composite config SHA-256 fingerprint for a workspace.

    Fingerprints every relevant loaded repairable configuration source:
    primary config (.agent/ralph-workflow.toml or ralph-workflow.toml),
    optional configs (.agent/agents.toml, .agent/mcp.toml, .agent/policy.toml,
    or root equivalents), and global configs (_global_config_path(),
    _global_agents_config_path()).

    Returns None if no tracked configuration file exists on disk.
    """
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
