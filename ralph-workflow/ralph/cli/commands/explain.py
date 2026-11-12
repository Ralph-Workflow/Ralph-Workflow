from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from types import ModuleType
    from typing import Protocol

    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.policy.explain import PolicyExplanation
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope

    class _LoadConfigFn(Protocol):
        def __call__(
            self,
            config_path: Path | None = None,
            cli_overrides: dict[str, object] | None = None,
            workspace_scope: WorkspaceScope | None = None,
        ) -> UnifiedConfig: ...

    class _ResolveWorkspaceScopeFn(Protocol):
        def __call__(self, start: Path | str | None = None) -> WorkspaceScope: ...

    class _LoadPolicyFn(Protocol):
        def __call__(
            self,
            config_dir: Path,
            config: UnifiedConfig | None = None,
        ) -> PolicyBundle: ...

    class _LoadPolicyForWorkspaceScopeFn(Protocol):
        def __call__(
            self,
            workspace_scope: WorkspaceScope,
            config: UnifiedConfig | None = None,
        ) -> PolicyBundle: ...

    class _ExplainPolicyFn(Protocol):
        def __call__(self, bundle: PolicyBundle) -> PolicyExplanation: ...

    class _RenderExplanationFn(Protocol):
        def __call__(self, explanation: PolicyExplanation) -> str: ...

    class _MakeDisplayContextFn(Protocol):
        def __call__(self) -> DisplayContext: ...

    class _ResolveActiveDisplayFn(Protocol):
        def __call__(self, override: object, ctx: DisplayContext) -> ParallelDisplay: ...


_BUNDLED_DEFAULTS_DIR: Path = Path(__file__).parent.parent.parent / "policy" / "defaults"


def _module_attr(module: ModuleType, attribute: str) -> object:
    namespace = cast("dict[str, object]", module.__dict__)
    return namespace[attribute]


def _load_config_loader() -> _LoadConfigFn:
    return cast(
        "_LoadConfigFn",
        _module_attr(import_module("ralph.config.loader"), "load_config"),
    )


def _load_resolve_workspace_scope() -> _ResolveWorkspaceScopeFn:
    return cast(
        "_ResolveWorkspaceScopeFn",
        _module_attr(import_module("ralph.workspace.scope"), "resolve_workspace_scope"),
    )


def _load_policy_loader() -> tuple[_LoadPolicyFn, _LoadPolicyForWorkspaceScopeFn]:
    module = import_module("ralph.policy.loader")
    return (
        cast("_LoadPolicyFn", _module_attr(module, "load_policy")),
        cast(
            "_LoadPolicyForWorkspaceScopeFn",
            _module_attr(module, "load_policy_for_workspace_scope"),
        ),
    )


def _load_explain_policy() -> _ExplainPolicyFn:
    return cast(
        "_ExplainPolicyFn",
        _module_attr(import_module("ralph.policy.explain"), "explain_policy"),
    )


def _load_renderers() -> tuple[_RenderExplanationFn, _RenderExplanationFn]:
    module = import_module("ralph.policy.render")
    return (
        cast("_RenderExplanationFn", _module_attr(module, "render_explanation_ascii")),
        cast("_RenderExplanationFn", _module_attr(module, "render_explanation_text")),
    )


def _load_policy_validation_error_type() -> type[Exception]:
    return cast(
        "type[Exception]",
        _module_attr(import_module("ralph.policy.validation"), "PolicyValidationError"),
    )


def _resolve_policy_dir() -> tuple[Path, bool]:
    try:
        scope = _load_resolve_workspace_scope()()
        policy_dir = scope.resolve_agent_file("pipeline.toml").parent
        if policy_dir.is_dir() and any(policy_dir.glob("*.toml")):
            return policy_dir, False
    except Exception:
        pass
    return _BUNDLED_DEFAULTS_DIR, True


def explain_command(
    policy_dir: Path | None = None,
    *,
    display_context: DisplayContext | None = None,
) -> int:
    load_policy, _load_policy_for_workspace_scope = _load_policy_loader()
    explain_policy = _load_explain_policy()
    render_explanation_ascii, render_explanation_text = _load_renderers()
    policy_validation_error_type = _load_policy_validation_error_type()
    ctx = display_context if display_context is not None else _load_make_display_context()()
    display = _load_resolve_active_display()(None, ctx)

    try:
        if policy_dir is not None:
            resolved_dir = policy_dir
            is_bundled = False
            if not resolved_dir.is_dir():
                display.emit_warning(f"Policy directory not found: {resolved_dir}")
                return 1
            bundle = load_policy(resolved_dir)
        else:
            resolved_dir, is_bundled = _resolve_policy_dir()
            bundle = load_policy(resolved_dir)
        if is_bundled:
            display.emit_status(
                "INFO: Using bundled default policy — no project-local .agent/*.toml files found"
            )
        display.emit_status(f"Policy source: {resolved_dir}")
        explanation = explain_policy(bundle)

        display.emit_status("")
        display.emit_status("WORKFLOW DIAGRAM")
        display.emit_status("=" * 70)
        display.emit_status(render_explanation_ascii(explanation))
        display.emit_status("")
        display.emit_status(render_explanation_text(explanation))
        return 0
    except policy_validation_error_type as exc:
        display.emit_warning(f"Policy validation error: {exc}")
        return 2
    except Exception as exc:
        display.emit_warning(f"Error loading policy: {exc}")
        return 1


def _load_make_display_context() -> _MakeDisplayContextFn:
    return cast(
        "_MakeDisplayContextFn",
        _module_attr(import_module("ralph.display.context"), "make_display_context"),
    )


def _load_resolve_active_display() -> _ResolveActiveDisplayFn:
    return cast(
        "_ResolveActiveDisplayFn",
        _module_attr(import_module("ralph.display.parallel_display"), "resolve_active_display"),
    )
