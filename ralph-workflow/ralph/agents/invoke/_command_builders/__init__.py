"""Per-transport CommandBuilder classes for agent invocation.

This module defines the CommandBuilder Protocol and the COMMAND_BUILDERS dispatch
dictionary that maps every AgentTransport value to its corresponding CommandBuilder class.
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
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
    return cast("AgentTransport", config.transport)


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


def _tokenize_pi_model_flag(model_flag: str) -> list[str]:
    """Tokenize a Pi ``--model`` flag into argv-safe tokens.

    Pi (https://pi.dev/docs/latest/usage) documents ``--model <pattern>``
    as a single-argv pattern.  The registry's
    ``_is_valid_pi_model_id`` validator already rejects whitespace,
    newlines, and multi-colon shapes so the value side of the flag is
    always a single token, but the flag string is built as
    ``"--model {shlex.quote(model_id)}"`` and may also be supplied by
    callers via ``BuildCommandOptions.model_flag``.

    ``shlex.split`` produces the canonical argv pair
    ``['--model', <value>]`` for both registry-built and caller-supplied
    flags without depending on the OpenCode-specific
    ``_normalize_opencode_model_flag`` helper (which strips an
    OpenCode-only ``opencode/`` prefix that pi.dev does not use).  This
    is the Pi-safe path so malformed flags do not leak garbage like
    ``['--model', "'foo", "bar'"]`` into downstream subprocess argv.
    """
    return shlex.split(model_flag)


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


@dataclass(frozen=True, slots=True)
class CommandBuilderSpec:
    base_argv: tuple[str, ...]
    format_flag: tuple[str, str] | None
    output_flag: str | None
    yolo_flag: str | None
    model_flag_template: str | None
    positional_prompt: bool
    print_flag: str | None
    extra_flags_before_prompt: tuple[str, ...] = ()


class ConfigurableCommandBuilder:
    """A command builder configured by a CommandBuilderSpec."""

    def __init__(self, spec: CommandBuilderSpec) -> None:
        self.spec = spec

    def _init_cmd(self, config: AgentConfig) -> list[str]:
        cmd = list(self.spec.base_argv)
        if not cmd:
            return []
        if "codex" in cmd[0]:
            return config.cmd.split()
        if "agy" in cmd[0]:
            return shlex.split(config.cmd)
        return [_agent_command_name(config), *cmd[1:]]

    def _build_yolo_session_flags(
        self,
        config: AgentConfig,
        options: _BuildCommandOptions,
    ) -> list[str]:
        flags: list[str] = []
        yolo = config.yolo_flag if config.yolo_flag is not None else self.spec.yolo_flag

        if "agy" in self.spec.base_argv[0]:
            if yolo is not None:
                flags.extend(_split_optional_flag(yolo))
            if config.session_flag and options.session_id:
                flags.extend(config.session_flag.format(options.session_id).split())
        else:
            if config.session_flag and options.session_id:
                flags.extend(config.session_flag.format(options.session_id).split())
            if yolo is not None:
                flags.extend(_split_optional_flag(yolo))
        return flags

    def _build_model_flag(
        self,
        config: AgentConfig,
        options: _BuildCommandOptions,
    ) -> list[str]:
        effective_model = options.model_flag or config.model_flag
        if not effective_model:
            return []
        if self.spec.model_flag_template is not None:
            if self.spec.base_argv and self.spec.base_argv[0] == "pi":
                return _tokenize_pi_model_flag(effective_model)
            if " " in effective_model or effective_model.startswith("-"):
                return _normalize_opencode_model_flag(effective_model)
            formatted = self.spec.model_flag_template.format(effective_model)
            return formatted.split()
        if "codex" in self.spec.base_argv[0]:
            return effective_model.split()
        return shlex.split(effective_model)

    def build(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: _BuildCommandOptions,
    ) -> list[str]:
        cmd = self._init_cmd(config)

        if options.pure and "opencode" in self.spec.base_argv[0]:
            cmd.append("--pure")

        if self.spec.format_flag is not None:
            cmd.extend(self.spec.format_flag)

        output_flag = (
            config.output_flag
            if config.output_flag is not None
            else self.spec.output_flag
        )
        if output_flag is not None and "opencode" not in self.spec.base_argv[0]:
            cmd.extend(_split_optional_flag(output_flag))

        cmd.extend(self._build_yolo_session_flags(config, options))

        if options.workspace_path is not None and "agy" in self.spec.base_argv[0]:
            cmd.extend(["--add-dir", str(options.workspace_path)])

        if options.verbose and config.verbose_flag:
            cmd.append(config.verbose_flag)

        cmd.extend(self._build_model_flag(config, options))

        if self.spec.extra_flags_before_prompt:
            cmd.extend(self.spec.extra_flags_before_prompt)

        print_flag = config.print_flag if config.print_flag is not None else self.spec.print_flag
        if print_flag is not None:
            cmd.append(print_flag)

        if self.spec.positional_prompt:
            prompt_text = _load_prompt_text(prompt_file, options.workspace_path)
            cmd.append(prompt_text)

        return cmd


class OpencodeCommandBuilder(ConfigurableCommandBuilder):
    """CommandBuilder for AgentTransport.OPENCODE."""

    SPEC = CommandBuilderSpec(
        base_argv=("opencode", "run"),
        format_flag=("--format", "json"),
        output_flag="--json-stream",
        yolo_flag=None,
        model_flag_template="--model {}",
        positional_prompt=True,
        print_flag=None,
        extra_flags_before_prompt=(),
    )

    def __init__(self) -> None:
        super().__init__(self.SPEC)


class NanocoderCommandBuilder(ConfigurableCommandBuilder):
    """CommandBuilder for AgentTransport.NANOCODER."""

    SPEC = CommandBuilderSpec(
        base_argv=("nanocoder", "--mode", "yolo", "run"),
        format_flag=None,
        output_flag=None,
        yolo_flag=None,
        model_flag_template=None,
        positional_prompt=True,
        print_flag=None,
        extra_flags_before_prompt=(),
    )

    def __init__(self) -> None:
        super().__init__(self.SPEC)


class CodexCommandBuilder(ConfigurableCommandBuilder):
    """CommandBuilder for AgentTransport.CODEX."""

    SPEC = CommandBuilderSpec(
        base_argv=("codex", "exec"),
        format_flag=None,
        output_flag="--json",
        yolo_flag="--dangerously-bypass-approvals-and-sandbox",
        model_flag_template=None,
        positional_prompt=True,
        print_flag=None,
        extra_flags_before_prompt=(),
    )

    def __init__(self) -> None:
        super().__init__(self.SPEC)


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


class AgyCommandBuilder(ConfigurableCommandBuilder):
    """CommandBuilder for AgentTransport.AGY."""

    SPEC = CommandBuilderSpec(
        base_argv=("agy",),
        format_flag=None,
        output_flag=None,
        yolo_flag="--dangerously-skip-permissions",
        model_flag_template=None,
        positional_prompt=True,
        print_flag="--print",
        extra_flags_before_prompt=(),
    )

    def __init__(self) -> None:
        super().__init__(self.SPEC)


class PiCommandBuilder(ConfigurableCommandBuilder):
    """CommandBuilder for AgentTransport.PI.

    The headless invocation is ``pi --mode json <prompt>`` per
    https://pi.dev/docs/latest/usage.  ``--mode json`` is modeled via
    :attr:`CommandBuilderSpec.output_flag` (a literal plan-compliant
    string); :class:`ConfigurableCommandBuilder` splits the string on
    whitespace via :func:`_split_optional_flag` so the two argv tokens
    ``--mode`` and ``json`` are emitted separately.  ``--approve`` is
    the documented non-interactive project-trust override (``-a``
    short form).  The prompt is a positional argument.
    """

    SPEC = CommandBuilderSpec(
        base_argv=("pi",),
        format_flag=None,
        output_flag="--mode json",
        yolo_flag="--approve",
        model_flag_template="--model {}",
        positional_prompt=True,
        print_flag=None,
        extra_flags_before_prompt=(),
    )

    def __init__(self) -> None:
        super().__init__(self.SPEC)


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
    AgentTransport.PI: PiCommandBuilder,
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
    "PiCommandBuilder",
]
