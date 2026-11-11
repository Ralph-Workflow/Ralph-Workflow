
from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ralph.config._toml_fallback import describe_config_failure, emit_config_failure_warning
from ralph.workspace.errors import WorkspaceContextError
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.registry import AgentRegistry
    from ralph.config.models import UnifiedConfig
    from ralph.mcp.effective_session_mcp_plan import EffectiveSessionMcpPlan
    from ralph.policy.models import PolicyBundle


@dataclass(frozen=True)
class WorkspaceContext:

    target_scope: WorkspaceScope
    config: UnifiedConfig
    policy_bundle: PolicyBundle
    registry: AgentRegistry
    effective_mcp_plan: EffectiveSessionMcpPlan
    prompt_path: Path

    @property
    def root(self) -> Path:
        return self.target_scope.root


@contextmanager
def workspace_context(target_root: Path) -> Iterator[WorkspaceContext]:
    from ralph.agents.registry import (
        AgentRegistry,
    )
    from ralph.config.loader import (
        ConfigTomlError,
        _attribute_validation_error_to_layer,
        _global_config_path,
        load_config,
        load_toml,
    )
    from ralph.mcp.artifacts.file_backend import (
        DEFAULT_FILE_BACKEND,
    )
    from ralph.mcp.session_plan import (
        resolve_effective_session_mcp_plan,
    )
    from ralph.policy.loader import (
        load_policy_for_workspace_scope,
    )

    target_root = Path(target_root)
    if not DEFAULT_FILE_BACKEND.exists(target_root):
        raise FileNotFoundError(f"workspace_context: target worktree does not exist: {target_root}")

    target_scope = resolve_workspace_scope(target_root)
    if not DEFAULT_FILE_BACKEND.exists(target_scope.root):
        raise ValueError(
            f"workspace_context: target workspace root does not exist: {target_scope.root}"
        )

    try:
        config = load_config(workspace_scope=target_scope)
        policy_bundle = load_policy_for_workspace_scope(target_scope, config=config)
        registry = AgentRegistry.from_config(config)
        effective_mcp_plan = resolve_effective_session_mcp_plan(target_scope.root)
    except (ConfigTomlError, OSError) as exc:
        msg = str(exc)
        m = re.search(r"Ralph could not read (.*?):", msg)
        matched_path = ""
        if m is not None:
            raw_match: object = m.group(1)
            if isinstance(raw_match, str):
                matched_path = raw_match.strip()
        file_path = (
            Path(matched_path)
            if matched_path
            else target_scope.root / ".agent" / "ralph-workflow.toml"
        )
        fix_str = f"fix {file_path} then run ralph --check-config"
        emit_config_failure_warning(
            file_path,
            kind="ralph-workflow",
            error_detail=describe_config_failure(exc),
            behaviour=(
                "strict configuration behaviour in effect "
                "(this file has no built-in default fallback)"
            ),
            fix_hint=f"To continue: {fix_str}",
            exc=exc,
        )
        raise WorkspaceContextError(
            file=file_path,
            reason=msg,
            fix=fix_str,
        ) from exc
    except SystemExit as exc:
        cause = exc.__cause__
        local_path = target_scope.root / ".agent" / "ralph-workflow.toml"
        file_path = local_path
        reason_str = str(cause) if cause is not None else str(exc)
        if cause is not None and isinstance(cause, ValidationError):
            try:
                agents_path = target_scope.root / ".agent" / "agents.toml"
                file_layers = [
                    (local_path, load_toml(local_path)),
                    (_global_config_path(), load_toml(_global_config_path())),
                    (agents_path, load_toml(agents_path)),
                ]
                file_path = _attribute_validation_error_to_layer(
                    cause,
                    local_path,
                    file_layers,
                    None,
                )
            except Exception:
                file_path = local_path
        fix_str = f"fix {file_path} then run ralph --check-config"
        emit_config_failure_warning(
            file_path,
            kind="ralph-workflow",
            error_detail=describe_config_failure(exc),
            behaviour=(
                "strict configuration behaviour in effect "
                "(this file has no built-in default fallback)"
            ),
            fix_hint=f"To continue: {fix_str}",
            exc=exc,
        )
        raise WorkspaceContextError(
            file=file_path,
            reason=f"configuration validation error in {file_path}: {reason_str}",
            fix=fix_str,
        ) from exc
    prompt_path = target_scope.root / "PROMPT.md"

    yield WorkspaceContext(
        target_scope=target_scope,
        config=config,
        policy_bundle=policy_bundle,
        registry=registry,
        effective_mcp_plan=effective_mcp_plan,
        prompt_path=prompt_path,
    )


__all__ = ["WorkspaceContext", "WorkspaceContextError", "workspace_context"]
