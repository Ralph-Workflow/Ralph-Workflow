"""Command building for agent invocation."""

from __future__ import annotations

import json
import shlex
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

__all__ = ["_agent_transport", "claude_mcp_config"]

from ralph.agents.invoke._command_builders import COMMAND_BUILDERS
from ralph.agents.invoke._errors import UnsupportedMcpTransportError
from ralph.agents.invoke._types import _BuildCommandOptions
from ralph.config.enums import AgentTransport
from ralph.mcp.transport.claude import claude_mcp_config

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig


def _agent_transport(config: AgentConfig) -> AgentTransport:
    return cast("AgentTransport", config.transport)


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
            ],
        },
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

    builder_cls = COMMAND_BUILDERS[transport]
    return builder_cls().build(config, prompt_file, options=build_options)


def _command_for_log(config: AgentConfig, cmd: list[str], prompt_file: str) -> str:
    logged_cmd = list(cmd)
    if (
        _agent_transport(config)
        in {
            AgentTransport.OPENCODE,
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
