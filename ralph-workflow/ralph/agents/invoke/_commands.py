"""Command building for agent invocation."""

from __future__ import annotations

import json
import shlex
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.agents.invoke._errors import UnsupportedMcpTransportError
from ralph.agents.invoke._process_reader import _agent_command_name
from ralph.agents.invoke._types import _BuildCommandOptions
from ralph.config.enums import AgentTransport
from ralph.mcp.transport.claude import claude_mcp_config

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig

_MODELED_FLAG_PARTS = 2
_HEADLESS_CLAUDE_PRINT_FLAGS = frozenset({"-p", "--print"})


def _agent_transport(config: AgentConfig) -> AgentTransport:
    transport = config.transport
    if transport is None:
        return AgentTransport.GENERIC
    return transport


def _shell_single_quote(value: str) -> str:
    escaped = value.replace("'", "'\\''")
    return f"'{escaped}'"


def _interactive_stop_sentinel_path(session_id: str) -> Path:
    return Path(tempfile.gettempdir()) / f"ralph-claude-interactive-{session_id}.done"


def _python_hook_command(source: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {_shell_single_quote(source)}"


def _interactive_permission_request_hook_command() -> str:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        }
    }
    return _python_hook_command(
        f"import json; print(json.dumps({payload!r}, separators=(',', ':')))"
    )


def _interactive_default_settings(sentinel_path: Path) -> dict[str, object]:
    stop_command = _python_hook_command(
        f"from pathlib import Path; Path({str(sentinel_path)!r}).touch()"
    )
    permission_command = _interactive_permission_request_hook_command()
    return {
        "skipDangerousModePermissionPrompt": True,
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": stop_command,
                        }
                    ]
                }
            ],
            "PermissionRequest": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": permission_command,
                        }
                    ]
                }
            ]
        }
    }


def _merge_interactive_settings_json(settings_json: str | None, sentinel_path: Path) -> str:
    settings = _interactive_default_settings(sentinel_path)
    if settings_json is None:
        return json.dumps(settings)
    try:
        parsed_obj = cast("object", json.loads(settings_json))
    except json.JSONDecodeError:
        return settings_json
    if not isinstance(parsed_obj, dict):
        return settings_json
    parsed_dict = cast("dict[str, object]", parsed_obj)

    merged = dict(parsed_dict)
    merged.setdefault("skipDangerousModePermissionPrompt", True)

    parsed_hooks = parsed_dict.get("hooks")
    existing_hooks = (
        cast("dict[str, object]", parsed_hooks) if isinstance(parsed_hooks, dict) else {}
    )
    default_hooks = settings["hooks"]
    assert isinstance(default_hooks, dict)
    merged_hooks: dict[str, object] = dict(existing_hooks)
    for event_name, default_value in default_hooks.items():
        merged_hooks.setdefault(event_name, default_value)
    merged["hooks"] = merged_hooks
    return json.dumps(merged)


def _interactive_stop_hook_settings(sentinel_path: Path) -> str:
    return _merge_interactive_settings_json(None, sentinel_path)


def _resolve_prompt_path(prompt_file: str, workspace_path: Path | None) -> Path:
    prompt_path = Path(prompt_file)
    if prompt_path.is_absolute() or workspace_path is None:
        return prompt_path
    return workspace_path / prompt_path


def _sidecar_path_for_prompt(prompt_path: Path) -> Path | None:
    if not prompt_path.name.endswith("_prompt.md"):
        return None
    normalized = prompt_path.stem.removesuffix("_prompt")
    return prompt_path.parent / f"{normalized}_multimodal_handoff.json"


def _read_multimodal_sidecar(
    prompt_file: str,
    workspace_path: Path | None,
) -> list[dict[str, object]] | None:
    resolved = _resolve_prompt_path(prompt_file, workspace_path)
    sidecar = _sidecar_path_for_prompt(resolved)
    if sidecar is None or not sidecar.exists():
        return None
    try:
        data: dict[str, object] = json.loads(sidecar.read_text(encoding="utf-8"))
        artifacts = data.get("artifacts")
        if isinstance(artifacts, list) and artifacts:
            return cast("list[dict[str, object]]", artifacts)
        return None
    except Exception:
        return None


def _build_multimodal_appendix(artifacts: list[dict[str, object]]) -> str:
    lines = [
        "",
        "",
        "## Multimodal Artifacts",
        "",
        "The following artifacts are available via Ralph's MCP surface.",
        "Retrieve each artifact by calling the read_media tool"
        " with path=<ralph://media/...> replay handle:",
        "",
    ]
    for entry in artifacts:
        modality = entry.get("modality", "unknown")
        title = entry.get("title", "untitled")
        uri = entry.get("uri", "")
        delivery = entry.get("delivery", "resource_reference_replay")
        block_type = entry.get("block_type", "")
        reason = entry.get("reason", "")
        failure_kind = entry.get("failure_kind", "")
        lines.append(f"- [{modality}] {title}")
        lines.append(f"  path={uri}")
        lines.append(f"  Delivery: {delivery}")
        if block_type:
            lines.append(f"  Block-type: {block_type}")
        if failure_kind == "unsupported_runtime_seam":
            reason_suffix = f" Reason: {reason}" if reason else ""
            lines.append(
                f"  Note: the upstream artifact exists but cannot be delivered"
                f" through the active runtime seam.{reason_suffix}"
                " Do not use read_media, replay handles, or typed blocks for this artifact."
            )
        elif delivery == "resource_reference_replay":
            lines.append(
                "  Note: if the artifact is from a previous session it may not be"
                " replayable; read_media will return an explicit"
                " missing_replay_source failure in that case."
            )
        elif delivery == "typed_block":
            block_type_hint = f" (block_type={block_type!r})" if block_type else ""
            lines.append(
                f"  Note: call read_media with this path to receive a typed block"
                f"{block_type_hint} for direct delivery to the model."
            )
        elif delivery == "resource_reference":
            lines.append(
                "  Note: this artifact references an external URI; the model may"
                " access it directly via the URI without calling read_media."
            )
        elif delivery == "unsupported":
            reason_suffix = f" Reason: {reason}" if reason else ""
            lines.append(
                f"  Note: this modality is unsupported by the active provider;"
                f"{reason_suffix}"
                " read_media will return an explicit unsupported_modality failure."
            )
        lines.append("")
    return "\n".join(lines)


def _extend_claude_transport_flags(
    cmd: list[str],
    transport: AgentTransport,
    build_options: _BuildCommandOptions,
) -> None:
    if (
        transport not in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE)
        or build_options.mcp_endpoint is None
    ):
        return

    # Claude/CCS non-interactive MCP mode is brittle around `--tools ""` combined
    # with `--allowedTools`. We only emit the tool restriction flags when live MCP
    # tool discovery succeeds and yields a non-empty allowlist; otherwise we keep the
    # strict MCP server isolation but avoid the known empty-tool edge case entirely.
    cmd.extend(
        [
            "--mcp-config",
            claude_mcp_config(
                build_options.mcp_endpoint,
                workspace_path=build_options.workspace_path,
            ),
            "--strict-mcp-config",
        ]
    )
    if build_options.allowed_mcp_tool_names:
        cmd.extend(
            [
                "--tools",
                "",
                "--allowedTools",
                ",".join(build_options.allowed_mcp_tool_names),
            ]
        )


def _append_transport_prompt_arg(
    cmd: list[str],
    transport: AgentTransport,
    prompt_file: str,
    build_options: _BuildCommandOptions,
) -> None:
    if (
        transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE)
        and build_options.mcp_endpoint
    ):
        cmd.append("--")
        prompt_text = _load_prompt_text(prompt_file, build_options.workspace_path)
        cmd.append(prompt_text)
        return
    cmd.append(prompt_file)


def _load_prompt_text(prompt_file: str, workspace_path: Path | None) -> str:
    text = _resolve_prompt_path(prompt_file, workspace_path).read_text(encoding="utf-8")
    artifacts = _read_multimodal_sidecar(prompt_file, workspace_path)
    if artifacts:
        text += _build_multimodal_appendix(artifacts)
    return text


def _split_optional_flag(flag: str | None) -> list[str]:
    if not flag:
        return []
    return shlex.split(flag)


def _command_already_enables_print_mode(cmd: list[str]) -> bool:
    return any(part in _HEADLESS_CLAUDE_PRINT_FLAGS for part in cmd)


def _normalize_opencode_model_flag(model_flag: str) -> list[str]:
    parts = model_flag.split()
    if len(parts) == _MODELED_FLAG_PARTS and parts[0] in {"-m", "--model"}:
        return [parts[0], parts[1].removeprefix("opencode/")]
    return parts


def _build_opencode_command(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: _BuildCommandOptions,
) -> list[str]:
    prompt_text = _load_prompt_text(prompt_file, options.workspace_path)
    cmd = [_agent_command_name(config), "run"]
    if options.pure:
        cmd.append("--pure")
    cmd.extend(["--format", "json"])

    if config.session_flag and options.session_id:
        cmd.extend(config.session_flag.format(options.session_id).split())

    cmd.extend(_split_optional_flag(config.yolo_flag))

    if options.verbose and config.verbose_flag:
        cmd.append(config.verbose_flag)

    effective_model = options.model_flag or config.model_flag
    if effective_model:
        cmd.extend(_normalize_opencode_model_flag(effective_model))

    cmd.append(prompt_text)
    return cmd


def _build_nanocoder_command(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: _BuildCommandOptions,
) -> list[str]:
    prompt_text = _load_prompt_text(prompt_file, options.workspace_path)
    cmd = [_agent_command_name(config), "--mode", "auto-accept", "run"]

    effective_model = options.model_flag or config.model_flag
    if effective_model:
        cmd.extend(shlex.split(effective_model))

    cmd.append(prompt_text)
    return cmd


def _build_codex_command(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: _BuildCommandOptions,
) -> list[str]:
    prompt_text = _load_prompt_text(prompt_file, options.workspace_path)
    cmd = config.cmd.split()
    if config.output_flag is not None:
        cmd.append(config.output_flag)

    cmd.extend(_split_optional_flag(config.yolo_flag))

    effective_model = options.model_flag or config.model_flag
    if effective_model:
        cmd.extend(effective_model.split())

    cmd.append(prompt_text)
    return cmd


def _build_agy_command(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: _BuildCommandOptions,
) -> list[str]:
    """Build the AGY command line.

    AGY uses: agy [--dangerously-skip-permissions] [--add-dir <path>] [--verbose] --print <prompt>
    """
    cmd = config.cmd.split()
    cmd.extend(_split_optional_flag(config.yolo_flag))
    if config.session_flag and options.session_id:
        cmd.extend(config.session_flag.format(options.session_id).split())
    if options.workspace_path is not None:
        cmd.extend(["--add-dir", str(options.workspace_path)])
    if options.verbose and config.verbose_flag:
        cmd.append(config.verbose_flag)
    if config.print_flag:
        cmd.append(config.print_flag)
    prompt_text = _load_prompt_text(prompt_file, options.workspace_path)
    cmd.append(prompt_text)
    return cmd


def _build_claude_interactive_command(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: _BuildCommandOptions,
) -> list[str]:
    cmd = config.cmd.split()
    cmd.extend(_split_optional_flag(config.yolo_flag))
    _extend_claude_transport_flags(cmd, AgentTransport.CLAUDE_INTERACTIVE, options)
    if options.verbose and config.verbose_flag:
        cmd.append(config.verbose_flag)
    if config.session_flag and options.session_id:
        cmd.extend(config.session_flag.format(options.session_id).split())
    if options.settings_json is not None:
        cmd.extend(["--settings", options.settings_json])
    if options.system_prompt_file:
        cmd.extend(["--append-system-prompt-file", options.system_prompt_file])
    effective_model = options.model_flag or config.model_flag
    if effective_model:
        cmd.extend(effective_model.split())
    _append_transport_prompt_arg(cmd, AgentTransport.CLAUDE_INTERACTIVE, prompt_file, options)
    return cmd


def _build_command(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: _BuildCommandOptions | None = None,
) -> list[str]:
    """Build the command line for agent invocation.

    Args:
        config: Agent configuration.
        prompt_file: Path to prompt file.

    Returns:
        List of command arguments.
    """
    build_options = options or _BuildCommandOptions()
    transport = _agent_transport(config)
    if build_options.mcp_endpoint and transport == AgentTransport.GENERIC:
        raise UnsupportedMcpTransportError(
            "Ralph MCP endpoint provided for agent without a supported transport adapter"
        )

    specialized_builders = {
        AgentTransport.OPENCODE: _build_opencode_command,
        AgentTransport.NANOCODER: _build_nanocoder_command,
        AgentTransport.CODEX: _build_codex_command,
        AgentTransport.CLAUDE_INTERACTIVE: _build_claude_interactive_command,
        AgentTransport.AGY: _build_agy_command,
    }
    specialized_builder = specialized_builders.get(transport)
    if specialized_builder is not None:
        return specialized_builder(
            config,
            prompt_file,
            options=build_options,
        )

    cmd = config.cmd.split()
    if transport == AgentTransport.CLAUDE and config.output_flag is not None:
        cmd.append(config.output_flag)

    if config.print_flag and not _command_already_enables_print_mode(cmd):
        cmd.append(config.print_flag)

    if config.streaming_flag:
        cmd.append(config.streaming_flag)

    if config.session_flag and build_options.session_id:
        cmd.extend(config.session_flag.format(build_options.session_id).split())

    cmd.extend(_split_optional_flag(config.yolo_flag))

    if build_options.verbose and config.verbose_flag:
        cmd.append(config.verbose_flag)

    _extend_claude_transport_flags(cmd, transport, build_options)

    if transport == AgentTransport.CLAUDE and build_options.system_prompt_file:
        cmd.extend(["--append-system-prompt-file", build_options.system_prompt_file])

    effective_model = build_options.model_flag or config.model_flag
    if effective_model:
        cmd.extend(effective_model.split())

    _append_transport_prompt_arg(cmd, transport, prompt_file, build_options)
    return cmd


def _command_for_log(config: AgentConfig, cmd: list[str], prompt_file: str) -> str:
    logged_cmd = list(cmd)
    if (
        _agent_transport(config)
        in {
            AgentTransport.OPENCODE,
            AgentTransport.NANOCODER,
            AgentTransport.CODEX,
            AgentTransport.CLAUDE,
            AgentTransport.CLAUDE_INTERACTIVE,
            AgentTransport.AGY,
        }
        and logged_cmd
    ):
        logged_cmd[-1] = prompt_file
    return " ".join(logged_cmd)


def check_agent_available(config: AgentConfig) -> bool:
    """Check if an agent command is available.

    Args:
        config: Agent configuration.

    Returns:
        True if agent command exists and is executable.
    """
    cmd = config.cmd.split()
    if not cmd:
        return False
    return shutil.which(cmd[0]) is not None
