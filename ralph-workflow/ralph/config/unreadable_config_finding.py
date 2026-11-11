"""Findings for configuration files Ralph cannot read or parse.

The config-scan seam inspects every configuration, policy, and MCP layer
for a file that is unreadable or malformed, reports each one as a typed
finding, and never fails fast: the run continues on built-in defaults for
optional files or fails loudly for strict files while the operator sees
exactly which file needs fixing.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config._config_filenames import (
    _GLOBAL_AGENTS_FILENAME,
    _GLOBAL_ARTIFACTS_FILENAME,
    _GLOBAL_CONFIG_FILENAME,
    _GLOBAL_MCP_FILENAME,
    _GLOBAL_PIPELINE_FILENAME,
    _LOCAL_CONFIG_FILENAME,
    _LOCAL_MCP_FILENAME,
)
from ralph.config._paths import resolve_global_config_dir
from ralph.config._toml_fallback import (
    _format_error_detail,
    emit_config_failure_warning,
)
from ralph.project_policy.markers import PORTFOLIO_PATH

if TYPE_CHECKING:
    from ralph.workspace.scope import WorkspaceScope


def _is_strict_config_file(path: Path, kind: str) -> bool:
    """Return whether this configuration file is strict and must fail loudly."""
    return kind in ("config", "ralph-workflow") or path.name in (
        _GLOBAL_CONFIG_FILENAME,
        _LOCAL_CONFIG_FILENAME,
        "ralph-workflow.toml",
    )


@dataclass(frozen=True)
class UnreadableConfigFinding:
    """Represents an unreadable or invalid configuration file finding.

    Attributes:
        path: Path to the offending configuration file.
        kind: Category or role of the configuration layer.
        error: Underlying exception encountered when reading or parsing.
    """

    path: Path
    kind: str
    error: BaseException

    def format_line(self) -> str:
        """Format the finding as a path and error string."""
        return f"{self.path}: {self.error}"


def collect_unreadable_config_files(
    *,
    config_path: Path | None = None,
    workspace_scope: WorkspaceScope | None = None,
    global_dir: Path | None = None,
    display: object | None = None,
) -> list[UnreadableConfigFinding]:
    """Inspect all configuration, policy, and MCP layers for unreadable/invalid files.

    Aggregates errors across:
      - config & agents layers
      - global / local layers
      - policy layers (pipeline, agents, artifacts, portfolio)
      - MCP layers (global and local)

    For every unreadable or malformed file found, emits a WARN log record and
    collects the finding without failing fast.

    Returns:
        List of UnreadableConfigFinding instances naming every unreadable file.
    """
    candidates = _gather_candidate_config_paths(
        config_path=config_path,
        workspace_scope=workspace_scope,
        global_dir=global_dir,
    )

    seen: set[Path] = set()
    deduped_candidates: list[tuple[Path, str]] = []
    for path, kind in candidates:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped_candidates.append((path, kind))

    findings: list[UnreadableConfigFinding] = []
    for path, kind in deduped_candidates:
        try:
            if not path.is_file():
                continue
        except OSError:
            try:
                if not path.exists():
                    continue
            except OSError:
                pass

        error = _inspect_config_file_readability(path, kind)
        if error is not None:
            error_detail = _format_error_detail(error)
            is_strict = _is_strict_config_file(path, kind)
            warn_kind = "ralph-workflow" if is_strict else kind
            if is_strict:
                behaviour = (
                    "strict configuration behaviour in effect "
                    "(this file has no built-in default fallback)"
                )
                fix_hint = f"To continue: fix {path} then run ralph --check-config"
            else:
                behaviour = "using built-in default"
                fix_hint = "Fix: run `ralph --check-config`."

            warned = emit_config_failure_warning(
                path,
                kind=warn_kind,
                error_detail=error_detail,
                behaviour=behaviour,
                fix_hint=fix_hint,
                exc=error,
            )
            if warned and display is not None and hasattr(display, "emit_warning"):
                display.emit_warning(
                    f"Failed to load {warn_kind} configuration from {path}: {error_detail}; "
                    f"{behaviour}. {fix_hint}"
                )
            findings.append(UnreadableConfigFinding(path=path, kind=warn_kind, error=error))

    return findings


def _gather_candidate_config_paths(
    *,
    config_path: Path | None,
    workspace_scope: WorkspaceScope | None,
    global_dir: Path | None,
) -> list[tuple[Path, str]]:
    resolved_global_dir = global_dir or resolve_global_config_dir()
    candidates: list[tuple[Path, str]] = [
        (resolved_global_dir / _GLOBAL_CONFIG_FILENAME, "config"),
        (resolved_global_dir / _GLOBAL_AGENTS_FILENAME, "agents"),
        (resolved_global_dir / _GLOBAL_MCP_FILENAME, "mcp"),
        (resolved_global_dir / _GLOBAL_PIPELINE_FILENAME, "pipeline"),
        (resolved_global_dir / "pipeline.toml", "pipeline"),
        (resolved_global_dir / _GLOBAL_ARTIFACTS_FILENAME, "artifacts"),
        (resolved_global_dir / "artifacts.toml", "artifacts"),
    ]

    from ralph.workspace.scope import resolve_workspace_scope

    scope = workspace_scope
    if scope is None and config_path is None:
        try:
            scope = resolve_workspace_scope()
        except Exception:
            scope = None

    support_files: tuple[tuple[str, str], ...] = (
        (_LOCAL_CONFIG_FILENAME, "config"),
        ("agents.toml", "agents"),
        (_LOCAL_MCP_FILENAME, "mcp"),
        ("pipeline.toml", "pipeline"),
        ("artifacts.toml", "artifacts"),
        ("portfolio.toml", "portfolio"),
    )

    if config_path is not None:
        candidates.append((config_path, "config"))
        cfg_parent = config_path.parent
        candidates.extend((cfg_parent / fn, kind) for fn, kind in support_files)
        candidates.append((cfg_parent.parent / PORTFOLIO_PATH, "portfolio"))
        candidates.append((cfg_parent / PORTFOLIO_PATH, "portfolio"))
        if (cfg_parent / ".agent").is_dir():
            candidates.extend((cfg_parent / ".agent" / fn, kind) for fn, kind in support_files)

    if scope is not None:
        candidates.append((scope.local_config_path, "config"))
        candidates.extend((prop, "config") for prop in scope.propagated_config_paths)

        local_agent_dir = scope.root / ".agent"
        candidates.extend((local_agent_dir / fn, kind) for fn, kind in support_files)
        if scope.local_config_path.parent != local_agent_dir:
            candidates.extend(
                (scope.local_config_path.parent / fn, kind) for fn, kind in support_files
            )
        candidates.append((scope.root / PORTFOLIO_PATH, "portfolio"))
        candidates.append((scope.root / "portfolio.toml", "portfolio"))
    elif config_path is None:
        cwd_agent = Path.cwd() / ".agent"
        candidates.extend((cwd_agent / fn, kind) for fn, kind in support_files)
        candidates.extend((Path.cwd() / fn, kind) for fn, kind in support_files)
        candidates.append((Path.cwd() / PORTFOLIO_PATH, "portfolio"))

    return candidates


def _validate_config_file_schema(
    path: Path, kind: str, data: dict[str, object], text: str
) -> BaseException | None:
    try:
        if kind == "agents":
            if path.name == "ralph-workflow-agents.toml":
                from ralph.config.loader import _drop_cli_only_fields
                from ralph.config.models import UnifiedConfig

                cleaned = dict(data)
                _drop_cli_only_fields(cleaned)
                UnifiedConfig.model_validate(cleaned)
            else:
                from ralph.policy._policy_validation import validate_agents

                validate_agents(data)
        elif kind == "mcp":
            from ralph.config.mcp_loader import _validate_mcp_layer

            _validate_mcp_layer(path, data, strict=True)
        elif kind == "pipeline":
            from ralph.policy._policy_validation import validate_pipeline
            from ralph.policy.loader import _merge_mapping_defaults, cached_default_pipeline_data

            merged = _merge_mapping_defaults(cached_default_pipeline_data(), data)
            validate_pipeline(merged, timeboxes_explicit=False)
        elif kind == "artifacts":
            from ralph.policy._policy_validation import validate_artifacts
            from ralph.policy.loader import _merge_mapping_defaults, cached_default_artifacts_data

            merged = _merge_mapping_defaults(cached_default_artifacts_data(), data)
            validate_artifacts(merged)
        elif kind == "portfolio":
            from ralph.project_policy.portfolio import load_portfolio_toml

            load_portfolio_toml(path, strict_optional_files=True)
        elif kind in ("config", "ralph-workflow"):
            from ralph.config.loader import _convert_legacy_config, _drop_cli_only_fields
            from ralph.config.models import UnifiedConfig

            cleaned = _convert_legacy_config(data)
            _drop_cli_only_fields(cleaned)
            UnifiedConfig.model_validate(cleaned)
    except (Exception, SystemExit) as exc:
        return exc
    return None


def _inspect_config_file_readability(path: Path, kind: str = "config") -> BaseException | None:
    try:
        # filesystem-read-ok: test readability of config layer
        raw_bytes = path.read_bytes()
    except (OSError, UnicodeDecodeError) as exc:
        return exc

    try:
        text = raw_bytes.decode("utf-8")
        data: dict[str, object] = tomllib.loads(text)
    except (ValueError, UnicodeDecodeError) as exc:
        return exc

    return _validate_config_file_schema(path, kind, data, text)


__all__ = ["UnreadableConfigFinding", "collect_unreadable_config_files"]
