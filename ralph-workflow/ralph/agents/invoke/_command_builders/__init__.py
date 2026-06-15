"""Per-transport CommandBuilder classes for agent invocation.

This module defines the CommandBuilder Protocol and the COMMAND_BUILDERS dispatch
dictionary that maps every AgentTransport value to its corresponding CommandBuilder class.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from ralph.agents.invoke._process_reader import _agent_command_name
from ralph.config.enums import AgentTransport
from ralph.mcp.tools.names import CLAUDE_NATIVE_TOOLS_TO_KEEP
from ralph.pro_support.prompt import resolve_effective_prompt_path

if TYPE_CHECKING:
    from ralph.agents.invoke._types import _BuildCommandOptions
    from ralph.config.models import AgentConfig


_MODELED_FLAG_PARTS = 2
_HEADLESS_CLAUDE_PRINT_FLAGS = frozenset({"-p", "--print"})


def _agent_transport(config: AgentConfig) -> AgentTransport:
    transport = config.transport
    if transport is None:
        return AgentTransport.GENERIC
    return transport


def _resolve_prompt_path(prompt_file: str, workspace_path: Path | None) -> Path:
    prompt_path = Path(prompt_file)
    if prompt_path.is_absolute() or workspace_path is None:
        return prompt_path
    if prompt_file == "PROMPT.md":
        return resolve_effective_prompt_path(workspace_path, os.environ)
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


def _extend_claude_transport_flags(
    cmd: list[str],
    transport: AgentTransport,
    build_options: _BuildCommandOptions,
) -> None:
    from ralph.agents.invoke._commands import claude_mcp_config  # noqa: PLC0415

    if (
        transport not in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE)
        or build_options.mcp_endpoint is None
    ):
        return

    cmd.extend(
        [
            "--mcp-config",
            claude_mcp_config(
                build_options.mcp_endpoint,
                workspace_path=build_options.workspace_path,
                unsafe_mode=build_options.unsafe_mode,
            ),
            "--strict-mcp-config",
        ]
    )
    if build_options.allowed_mcp_tool_names:
        cmd.extend(
            [
                "--tools",
                ",".join(CLAUDE_NATIVE_TOOLS_TO_KEEP),
                "--allowedTools",
                ",".join((*build_options.allowed_mcp_tool_names, *CLAUDE_NATIVE_TOOLS_TO_KEEP)),
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


@runtime_checkable
class CommandBuilder(Protocol):
    """Protocol for per-transport command building.

    Each transport-specific CommandBuilder implementation provides a build()
    method that constructs the agent command line as a list of strings.
    """

    def build(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: _BuildCommandOptions,
    ) -> list[str]:
        """Build the command line for agent invocation.

        Args:
            config: Agent configuration.
            prompt_file: Path to prompt file.
            options: Build command options.

        Returns:
            List of command arguments.
        """
        ...


class OpencodeCommandBuilder:
    """CommandBuilder for AgentTransport.OPENCODE."""

    def build(
        self,
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


class NanocoderCommandBuilder:
    """CommandBuilder for AgentTransport.NANOCODER."""

    def build(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: _BuildCommandOptions,
    ) -> list[str]:
        prompt_text = _load_prompt_text(prompt_file, options.workspace_path)
        cmd = [_agent_command_name(config), "--mode", "yolo", "run"]

        effective_model = options.model_flag or config.model_flag
        if effective_model:
            cmd.extend(shlex.split(effective_model))

        cmd.append(prompt_text)
        return cmd


class CodexCommandBuilder:
    """CommandBuilder for AgentTransport.CODEX."""

    def build(
        self,
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


class ClaudeInteractiveCommandBuilder:
    """CommandBuilder for AgentTransport.CLAUDE_INTERACTIVE."""

    def build(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: _BuildCommandOptions,
    ) -> list[str]:
        cmd = shlex.split(config.cmd)
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


class AgyCommandBuilder:
    """CommandBuilder for AgentTransport.AGY."""

    def build(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: _BuildCommandOptions,
    ) -> list[str]:
        cmd = shlex.split(config.cmd)
        cmd.extend(_split_optional_flag(config.yolo_flag))
        if config.session_flag and options.session_id:
            cmd.extend(config.session_flag.format(options.session_id).split())
        if options.workspace_path is not None:
            cmd.extend(["--add-dir", str(options.workspace_path)])
        if options.verbose and config.verbose_flag:
            cmd.append(config.verbose_flag)
        effective_model = options.model_flag or config.model_flag
        if effective_model:
            cmd.extend(shlex.split(effective_model))
        if config.print_flag:
            cmd.append(config.print_flag)
        prompt_text = _load_prompt_text(prompt_file, options.workspace_path)
        cmd.append(prompt_text)
        return cmd


class DefaultCommandBuilder:
    """Default CommandBuilder for AgentTransport.CLAUDE and AgentTransport.GENERIC.

    This builder handles the default command construction for CLAUDE and GENERIC
    transports when no specialized builder is needed.
    """

    def build(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: _BuildCommandOptions,
    ) -> list[str]:
        cmd = config.cmd.split()
        transport = _agent_transport(config)

        if transport == AgentTransport.CLAUDE and config.output_flag is not None:
            cmd.append(config.output_flag)

        if config.print_flag and not _command_already_enables_print_mode(cmd):
            cmd.append(config.print_flag)

        if config.streaming_flag:
            cmd.append(config.streaming_flag)

        if config.session_flag and options.session_id:
            cmd.extend(config.session_flag.format(options.session_id).split())

        cmd.extend(_split_optional_flag(config.yolo_flag))

        if options.verbose and config.verbose_flag:
            cmd.append(config.verbose_flag)

        _extend_claude_transport_flags(cmd, transport, options)

        if transport == AgentTransport.CLAUDE and options.system_prompt_file:
            cmd.extend(["--append-system-prompt-file", options.system_prompt_file])

        effective_model = options.model_flag or config.model_flag
        if effective_model:
            cmd.extend(effective_model.split())

        _append_transport_prompt_arg(cmd, transport, prompt_file, options)
        return cmd


COMMAND_BUILDERS: dict[AgentTransport, type[CommandBuilder]] = {
    AgentTransport.OPENCODE: OpencodeCommandBuilder,
    AgentTransport.NANOCODER: NanocoderCommandBuilder,
    AgentTransport.CODEX: CodexCommandBuilder,
    AgentTransport.CLAUDE_INTERACTIVE: ClaudeInteractiveCommandBuilder,
    AgentTransport.AGY: AgyCommandBuilder,
    AgentTransport.CLAUDE: DefaultCommandBuilder,
    AgentTransport.GENERIC: DefaultCommandBuilder,
}

__all__ = [
    "COMMAND_BUILDERS",
    "AgyCommandBuilder",
    "ClaudeInteractiveCommandBuilder",
    "CodexCommandBuilder",
    "CommandBuilder",
    "DefaultCommandBuilder",
    "NanocoderCommandBuilder",
    "OpencodeCommandBuilder",
]
