"""Default-gate end-to-end proof for Ralph-linked multimodal support."""

from __future__ import annotations

import base64
import functools
import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import ralph.agents.invoke as invoke_module
import ralph.prompts.materialize as materialize_module
from ralph.agents.invoke import (
    BuildCommandOptions,
    build_command,
    provider_allowed_mcp_tool_names,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.multimodal.capabilities import UNKNOWN_IDENTITY
from ralph.mcp.protocol import startup
from ralph.mcp.server.runtime import JsonRpcRequest, McpServer, ServerState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.prompts.debug_dump import (
    collect_media_entries_for_phase,
    multimodal_sidecar_path,
)
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from tests._support.typed_accessors import must_dict_list, must_mapping, must_str
from tests.integration._multimodal_e2e_fixtures import (
    TINY_PDF_BYTES,
    TINY_PNG_BYTES,
    build_multimodal_harness,
    install_media_backend,
    make_in_process_post_fn,
)

if TYPE_CHECKING:
    import pytest

    from ralph.mcp.protocol.session import AgentSession
    from ralph.prompts.materialize import MultimodalSidecarEntry
    from ralph.workspace.memory import MemoryWorkspace


def _rpc_result(
    server: McpServer,
    state_box: list[ServerState],
    method: str,
    params: dict[str, object],
    msg_id: int,
) -> Mapping[str, object]:
    response, next_state = server.handle_request(
        JsonRpcRequest(
            jsonrpc="2.0",
            method=method,
            params=params,
            msg_id=msg_id,
        ),
        state_box[0],
    )
    state_box[0] = next_state
    assert response is not None
    assert response.error is None
    return must_mapping(response.result)


def _first_content_block(result: Mapping[str, object]) -> Mapping[str, object]:
    return must_mapping(must_dict_list(result["content"])[0])


def _first_non_warning_block(
    result: Mapping[str, object],
) -> Mapping[str, object]:
    """Return the first content block that is NOT a ``WARNING:`` text block.

    S-7 (criterion 3): an UNKNOWN_IDENTITY degrades gracefully with a
    WARNING text block. Tests that assert on the underlying payload
    use this helper to skip past any leading WARNING block and find
    the real block to type-check.
    """
    for block in must_dict_list(result["content"]):
        if block.get("type") != "text":
            return must_mapping(block)
        text = must_str(block.get("text", ""))
        if not text.startswith("WARNING:"):
            return must_mapping(block)
    raise AssertionError("no non-warning content block found")


def _pipeline_policy() -> PipelinePolicy:
    return PipelinePolicy(
        entry_phase="development",
        phases={
            "development": PhaseDefinition(
                drain="development",
                role="execution",
                prompt_template="developer_iteration.jinja",
                transitions=PhaseTransition(on_success="complete"),
            )
        },
    )


def _workspace_key(workspace: MemoryWorkspace, path: Path) -> str:
    try:
        return str(path.relative_to(workspace.root))
    except ValueError:
        return str(path)


def _discover_default_tool_names(
    server: McpServer,
    state_box: list[ServerState],
    endpoint: str,
) -> set[str]:
    target = startup.parse_http_endpoint(endpoint)
    post_jsonrpc = functools.partial(
        startup.post_http_jsonrpc_with_session,
        post_fn=make_in_process_post_fn(server, state_box),
    )
    initialize_response, session_id = post_jsonrpc(
        endpoint,
        target,
        startup.initialize_request(),
    )
    assert initialize_response.get("error") is None
    initialized_response, session_id = post_jsonrpc(
        endpoint,
        target,
        startup.initialized_notification(),
        session_id=session_id,
    )
    assert initialized_response == {}
    tools_response, _ = post_jsonrpc(
        endpoint,
        target,
        startup.tools_list_request(),
        session_id=session_id,
    )
    tools_result = must_mapping(tools_response["result"])
    return {
        must_str(tool["name"])
        for tool in must_dict_list(tools_result["tools"])
    }


def _exercise_media_round_trip(
    server: McpServer,
    state_box: list[ServerState],
    session: AgentSession,
    workspace: MemoryWorkspace,
) -> tuple[str, list[MultimodalSidecarEntry]]:
    png_result = _rpc_result(
        server,
        state_box,
        "tools/call",
        {"name": "read_media", "arguments": {"path": "screenshot.png"}},
        3,
    )
    png_block = _first_content_block(png_result)
    assert png_block["type"] == "image"
    assert base64.b64decode(must_str(png_block["data"])) == TINY_PNG_BYTES

    session.model_identity = UNKNOWN_IDENTITY
    pdf_result = _rpc_result(
        server,
        state_box,
        "tools/call",
        {"name": "read_media", "arguments": {"path": "report.pdf"}},
        4,
    )
    # S-7 (criterion 3): an UNKNOWN_IDENTITY degrades gracefully with
    # a WARNING text block (prepended); skip past it to the
    # resource_reference payload.
    pdf_block = _first_non_warning_block(pdf_result)
    assert pdf_block["type"] == "resource_reference"
    assert pdf_block["delivery"] == "resource_reference_replay"
    pdf_uri = must_str(pdf_block["uri"])
    assert pdf_uri.startswith("ralph://media/")

    resources_result = _rpc_result(server, state_box, "resources/list", {}, 5)
    listed_uris = {
        must_str(resource["uri"])
        for resource in must_dict_list(resources_result["resources"])
    }
    assert pdf_uri in listed_uris

    replay_result = _rpc_result(
        server,
        state_box,
        "resources/read",
        {"uri": pdf_uri},
        6,
    )
    replay_block = must_mapping(must_dict_list(replay_result["contents"])[0])
    assert replay_block["uri"] == pdf_uri
    assert base64.b64decode(must_str(replay_block["blob"])) == TINY_PDF_BYTES

    media_entries = collect_media_entries_for_phase(workspace, "development")
    pdf_entry = next(entry for entry in media_entries if entry.uri == pdf_uri)
    assert pdf_entry.modality == "pdf"
    assert pdf_entry.delivery == "resource_reference_replay"
    assert workspace.is_file(pdf_entry.cache_path)
    return pdf_uri, media_entries


def _materialize_linked_prompt(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MemoryWorkspace,
    media_entries: list[MultimodalSidecarEntry],
    pdf_uri: str,
) -> str:
    def render_prompt(
        context: PromptPhaseContext,
        options: PromptPhaseOptions,
    ) -> str:
        del context, options
        return "# Development prompt\n"

    with monkeypatch.context() as materialize_patch:
        materialize_patch.setattr(
            materialize_module,
            "_render_prompt_for_phase",
            render_prompt,
        )
        prompt_path = materialize_prompt_for_phase(
            PromptPhaseContext(
                phase="development",
                workspace=workspace,
                pipeline_policy=_pipeline_policy(),
                session_caps=SessionCapabilities.defaults_for_drain(
                    SessionDrain.DEVELOPMENT
                ),
                workspace_root=workspace.root,
            ),
            PromptPhaseOptions(multimodal_entries=media_entries),
        )

    sidecar_path = multimodal_sidecar_path("development")
    assert workspace.exists(prompt_path)
    assert workspace.exists(sidecar_path)
    sidecar = must_mapping(json.loads(workspace.read(sidecar_path)))
    sidecar_uris = {
        must_str(artifact["uri"])
        for artifact in must_dict_list(sidecar["artifacts"])
    }
    assert pdf_uri in sidecar_uris
    return prompt_path


def _discover_provider_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    server: McpServer,
    endpoint: str,
    claude_config: AgentConfig,
) -> tuple[str, ...]:
    discovery_state = [ServerState.UNINITIALIZED]
    discovery_post = functools.partial(
        startup.post_http_jsonrpc_with_session,
        post_fn=make_in_process_post_fn(server, discovery_state),
    )
    with monkeypatch.context() as discovery_patch:
        discovery_patch.setattr(
            invoke_module,
            "post_http_jsonrpc_with_session",
            discovery_post,
        )
        allowed_tools = provider_allowed_mcp_tool_names(claude_config, endpoint)
    assert "mcp__ralph__read_media" in allowed_tools
    assert "mcp__ralph__read_image" in allowed_tools
    return allowed_tools


def _assert_linked_claude_command(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MemoryWorkspace,
    claude_config: AgentConfig,
    endpoint: str,
    prompt_path: str,
    pdf_uri: str,
    allowed_tools: tuple[str, ...],
) -> None:
    def read_workspace_text(path: Path, **kwargs: object) -> str:
        del kwargs
        return workspace.read(_workspace_key(workspace, path))

    def workspace_path_exists(path: Path, **kwargs: object) -> bool:
        del kwargs
        return workspace.exists(_workspace_key(workspace, path))

    with monkeypatch.context() as command_patch:
        command_patch.setattr(Path, "read_text", read_workspace_text)
        command_patch.setattr(Path, "exists", workspace_path_exists)
        command = build_command(
            claude_config,
            prompt_path,
            options=BuildCommandOptions(
                mcp_endpoint=endpoint,
                allowed_mcp_tool_names=allowed_tools,
            ),
        )
    assert "## Multimodal Artifacts" in command[-1]
    assert pdf_uri in command[-1]
    allowed_tools_value = command[command.index("--allowedTools") + 1]
    assert "mcp__ralph__read_media" in allowed_tools_value.split(",")


def test_ralph_multimodal_regression_default_harness_end_to_end_linkage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan S-5: every Ralph-linked multimodal layer remains connected by default."""
    server, workspace, backend, session = build_multimodal_harness()
    install_media_backend(monkeypatch, backend)
    endpoint = "http://127.0.0.1:9999/mcp"
    state_box = [ServerState.UNINITIALIZED]

    tool_names = _discover_default_tool_names(server, state_box, endpoint)
    assert "read_media" in tool_names
    assert "read_image" in tool_names

    pdf_uri, media_entries = _exercise_media_round_trip(
        server,
        state_box,
        session,
        workspace,
    )
    prompt_path = _materialize_linked_prompt(
        monkeypatch,
        workspace,
        media_entries,
        pdf_uri,
    )
    claude_config = AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE)
    allowed_tools = _discover_provider_allowlist(
        monkeypatch,
        server,
        endpoint,
        claude_config,
    )
    _assert_linked_claude_command(
        monkeypatch,
        workspace,
        claude_config,
        endpoint,
        prompt_path,
        pdf_uri,
        allowed_tools,
    )
