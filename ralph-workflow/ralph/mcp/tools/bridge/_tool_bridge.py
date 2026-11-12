from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.mcp.tools.bridge._lazy_tool_handler import LazyToolHandler
from ralph.mcp.tools.bridge._registered_tool import RegisteredTool
from ralph.mcp.tools.bridge._spec_helpers import _is_approved
from ralph.mcp.tools.bridge._tool_bridge_error import ToolBridgeError
from ralph.mcp.tools.bridge._tool_dispatch_error import ToolDispatchError
from ralph.mcp.tools.bridge._tool_registration_error import ToolRegistrationError
from ralph.mcp.tools.capability_denied_error import CapabilityDeniedError
from ralph.mcp.tools.invalid_params_error import InvalidParamsError
from ralph.mcp.tools.tool_content import ToolContent
from ralph.mcp.tools.tool_error import ToolError
from ralph.mcp.tools.tool_result import ToolResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.tools.bridge._registration_handler import RegistrationHandler
    from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
    from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
    from ralph.mcp.tools.bridge._tool_spec import ToolSpec
    from ralph.mcp.tools.bridge._types import JsonObject

_INVENTED_PREFIXES: tuple[str, ...] = ("ralph_mcp__", "mcp__ralph__", "ralph__", "ralph_", "ralph.")

_RETIRED_JSON_TOOL_REPLACEMENTS: dict[str, str] = {
    "ralph_submit_artifact": "ralph_submit_md_artifact",
    "ralph_submit_plan_section": "ralph_stage_md_artifact",
    "ralph_submit_plan_sections": "ralph_stage_md_artifact",
    "ralph_finalize_plan": "ralph_finalize_md_artifact",
    "ralph_get_plan_draft": "ralph_get_md_draft",
    "ralph_discard_plan_draft": "ralph_discard_md_draft",
    "ralph_validate_draft": "ralph_verify_md_artifact",
    "ralph_patch_step": "ralph_edit_md_artifact",
    "ralph_insert_plan_step": "ralph_edit_md_artifact",
    "ralph_replace_plan_step": "ralph_edit_md_artifact",
    "ralph_remove_plan_step": "ralph_edit_md_artifact",
    "ralph_move_plan_step": "ralph_edit_md_artifact",
    "ralph_edit_md_plan_step": "ralph_edit_md_artifact",
}


def _candidate_names(name: str) -> tuple[str, ...]:
    stripped = tuple(
        name[len(prefix) :] for prefix in _INVENTED_PREFIXES if name.startswith(prefix)
    )
    return (name, *stripped)


def _retired_replacement(name: str) -> str | None:
    for candidate in _candidate_names(name):
        replacement = _RETIRED_JSON_TOOL_REPLACEMENTS.get(candidate)
        if replacement is not None:
            return replacement
    return None


class ToolBridge:
    def __init__(self, session: object | None = None) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._session = session

    def register(self, metadata: ToolMetadata, handler: RegistrationHandler) -> None:
        name = metadata.definition.name
        if name in self._tools:
            raise ToolRegistrationError(f"Tool '{name}' is already registered")
        self._tools[name] = RegisteredTool(metadata=metadata, handler=handler)

    def register_spec(self, spec: ToolSpec, *, session: object, workspace: object) -> None:
        self.register(
            spec.metadata,
            LazyToolHandler(
                module_name=spec.module_name,
                handler_name=spec.handler_name,
                session=session,
                workspace=workspace,
            ),
        )

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolDispatchError(self._unknown_tool_message(name)) from exc

    def _unknown_tool_message(self, name: str) -> str:
        base = f"Tool '{name}' is not registered"
        replacement = _retired_replacement(name)
        if replacement is not None:
            return (
                f"{base}: it was removed in the markdown artifact migration. "
                f"Call '{replacement}' instead."
            )
        suggestion = self._suggest_registered_name(name)
        hint = f" Did you mean '{suggestion}'?" if suggestion else ""
        available = ", ".join(sorted(self._tools)) or "(none)"
        return f"{base}.{hint} Do not retry the same name. Available tools: {available}"

    def _suggest_registered_name(self, name: str) -> str | None:
        for candidate in _candidate_names(name)[1:]:
            if candidate in self._tools:
                return candidate
        return None

    def list_metadata(self) -> list[ToolMetadata]:
        return [
            tool.metadata for tool in self._tools.values() if self._is_tool_allowed(tool.metadata)
        ]

    def list_definitions(self) -> list[ToolDefinition]:
        return [
            tool.metadata.definition
            for tool in self._tools.values()
            if self._is_tool_allowed(tool.metadata)
        ]

    def dispatch(
        self,
        name: str,
        params: JsonObject | None = None,
        *,
        host_session: object | None = None,
        workspace: object | None = None,
    ) -> object:
        tool = self.get(name)
        session = host_session or self._session
        if not self._is_tool_allowed(tool.metadata, session=session):
            capability = tool.metadata.required_capability
            raise ToolDispatchError(f"Tool '{name}' requires capability '{capability}'")
        tool_params = dict(params or {})
        try:
            return tool.handler(host_session, workspace, tool_params)
        except ToolBridgeError:
            raise
        except (InvalidParamsError, CapabilityDeniedError):
            raise
        except ToolError as exc:
            return ToolResult(
                content=[ToolContent.text_content(str(exc))],
                is_error=True,
            )
        except Exception as exc:
            raise ToolDispatchError(f"Tool '{name}' failed: {exc}") from exc

    def _is_tool_allowed(self, metadata: ToolMetadata, session: object | None = None) -> bool:
        effective_session = session or self._session
        if effective_session is None:
            return True

        checker = cast(
            "Callable[[str], object] | None",
            getattr(effective_session, "check_capability", None),
        )
        if not callable(checker):
            return True

        return _is_approved(checker(metadata.required_capability))
