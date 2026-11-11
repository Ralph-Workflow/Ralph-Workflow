from __future__ import annotations

from importlib import import_module

from ralph.config.mcp_models import McpConfig
from ralph.mcp.tools.bridge import ToolSpec, tool_specs
from ralph.mcp.tools.coordination import ToolResult
from ralph.mcp.tools.names import ALL_RALPH_TOOLS
from tests.mcp._default_deny_invariant_helper__fakeworkspace import _FakeWorkspace


class _DenyAllSession:
    session_id = "deny-all-session"

    def check_capability(self, capability: str) -> str:
        return "denied"

    def check_edit_area(self, path: str) -> bool:
        return True


def _get_ralph_tool_specs() -> dict[str, ToolSpec]:
    all_specs = tool_specs(McpConfig())
    by_name: dict[str, ToolSpec] = {spec.metadata.definition.name: spec for spec in all_specs}
    return {name: by_name[name] for name in ALL_RALPH_TOOLS if name in by_name}


def test_every_native_tool_declares_required_capability() -> None:
    ralph_specs = _get_ralph_tool_specs()
    violations: list[str] = []

    for name in ALL_RALPH_TOOLS:
        spec = ralph_specs.get(name)
        if spec is None:
            violations.append(f"{name}: no ToolSpec found in tool_specs(McpConfig())")
            continue
        cap = getattr(spec.metadata, "required_capability", None)
        if not cap:
            violations.append(f"{name}: required_capability is missing or empty")

    assert violations == [], "Tools missing required_capability declaration:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


def test_deny_all_session_cannot_invoke_any_native_tool() -> None:
    deny_session = _DenyAllSession()
    fake_workspace = _FakeWorkspace()
    ralph_specs = _get_ralph_tool_specs()

    names_that_succeeded: list[str] = []

    for name, spec in ralph_specs.items():
        module = import_module(spec.module_name)
        handler = getattr(module, spec.handler_name)
        try:
            result = handler(deny_session, fake_workspace, {})
            if isinstance(result, ToolResult) and not result.is_error:
                names_that_succeeded.append(name)
        except Exception:
            pass

    assert names_that_succeeded == [], (
        "These tools returned is_error=False under a deny-all session (default-deny broken):\n"
        + "\n".join(f"  - {n}" for n in names_that_succeeded)
    )
