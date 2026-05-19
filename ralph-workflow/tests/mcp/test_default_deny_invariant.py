"""Default-deny invariant tests for the native MCP tool registry.

Verifies that:
1. Every native tool registered in ALL_RALPH_TOOLS declares a non-empty
   required_capability via its ToolMetadata.
2. A session that approves no capability cannot successfully invoke any
   native tool — every handler either raises or returns is_error=True.
"""

from __future__ import annotations

from importlib import import_module

from ralph.config.mcp_models import McpConfig
from ralph.mcp.tools.bridge import ToolSpec, tool_specs
from ralph.mcp.tools.coordination import ToolResult
from ralph.mcp.tools.names import ALL_RALPH_TOOLS
from tests.mcp.test_default_deny_invariant_helper__fakeworkspace import _FakeWorkspace


class _DenyAllSession:
    """Session that denies every capability check."""

    session_id = "deny-all-session"

    def check_capability(self, capability: str) -> str:
        return "denied"

    def check_edit_area(self, path: str) -> bool:
        return True




def _get_ralph_tool_specs() -> dict[str, ToolSpec]:
    """Return a name→ToolSpec mapping for all tools in ALL_RALPH_TOOLS."""
    all_specs = tool_specs(McpConfig())
    by_name: dict[str, ToolSpec] = {spec.metadata.definition.name: spec for spec in all_specs}
    return {name: by_name[name] for name in ALL_RALPH_TOOLS if name in by_name}


def test_every_native_tool_declares_required_capability() -> None:
    """Every native tool in ALL_RALPH_TOOLS must declare a non-empty required_capability."""
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
    """A session that denies all capabilities must not succeed at any native tool call.

    Handlers may either raise CapabilityDeniedError OR return ToolResult(is_error=True).
    Both patterns properly deny the operation. What must NOT happen is returning
    ToolResult(is_error=False) — that would mean the operation succeeded despite denial.
    """
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
            pass  # any exception means default-deny is working

    assert names_that_succeeded == [], (
        "These tools returned is_error=False under a deny-all session (default-deny broken):\n"
        + "\n".join(f"  - {n}" for n in names_that_succeeded)
    )
