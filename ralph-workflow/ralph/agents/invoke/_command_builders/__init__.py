from __future__ import annotations

import hashlib
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, cast, runtime_checkable

from ralph.agents.invoke._process_reader import _agent_command_name
from ralph.config.enums import AgentTransport
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.mcp.tools.names import CLAUDE_NATIVE_TOOLS_TO_KEEP
from ralph.mcp.transport.claude import claude_mcp_config
from ralph.mcp.transport.pi import pi_mcp_extension_path
from ralph.pro_support.prompt import resolve_effective_prompt_path

if TYPE_CHECKING:
    from ralph.agents.invoke._types import _BuildCommandOptions
    from ralph.config.models import AgentConfig


_MODELED_FLAG_PARTS = 2
_OPENCODE_MODEL_FLAG_NAMES: Final[frozenset[str]] = frozenset({"-m", "--model"})
_PI_MODEL_FLAG_PARTS = 2
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


def _load_prompt_with_master(
    prompt_file: str,
    workspace_path: Path | None,
    master_prompt_file: str | None,
) -> str:
    prompt = _load_prompt_text(prompt_file, workspace_path)
    if master_prompt_file is None:
        return prompt
    master_path = Path(master_prompt_file)
    if not master_path.is_absolute() and workspace_path is not None:
        master_path = workspace_path / master_path
    master = master_path.read_text(encoding="utf-8")
    return f"{master.rstrip()}\n\n{prompt}"


def _materialize_prompt_file(text: str, dest_path: Path) -> str:
    DEFAULT_FILE_BACKEND.mkdir(dest_path.parent, parents=True, exist_ok=True)
    write_text_if_changed(DEFAULT_FILE_BACKEND, dest_path, text, encoding="utf-8")
    return str(dest_path)


def materialize_argv_prompt(text: str, cwd: str | None) -> str:
    base = Path(cwd) if cwd is not None else Path.cwd()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return _materialize_prompt_file(text, base / ".agent" / "tmp" / f"prompt_{digest}_argv.md")


def _materialize_generic_prompt_with_master(
    prompt_file: str,
    workspace_path: Path | None,
    master_prompt_file: str,
) -> str:
    master_path = Path(master_prompt_file)
    if not master_path.is_absolute() and workspace_path is not None:
        master_path = workspace_path / master_path
    prompt_path = _resolve_prompt_path(prompt_file, workspace_path)
    combined_path = master_path.with_name(
        f"{master_path.stem}_{prompt_path.stem}_generic_prompt.md"
    )
    combined_prompt = _load_prompt_with_master(
        prompt_file,
        workspace_path,
        master_prompt_file,
    )
    return _materialize_prompt_file(combined_prompt, combined_path)


def _split_optional_flag(flag: str | None) -> list[str]:
    if not flag:
        return []
    return shlex.split(flag)


def _format_session_flag(
    session_flag: str | None,
    session_id: str | None,
) -> list[str]:
    if not session_flag or not session_id:
        return []
    formatted = session_flag.format(shlex.quote(session_id))
    return shlex.split(formatted)


def _command_already_enables_print_mode(cmd: list[str]) -> bool:
    return any(part in _HEADLESS_CLAUDE_PRINT_FLAGS for part in cmd)


def _tokenize_opencode_model_flag(model_flag: str) -> list[str]:
    parts = shlex.split(model_flag)
    if len(parts) != _MODELED_FLAG_PARTS or parts[0] not in _OPENCODE_MODEL_FLAG_NAMES:
        raise ValueError(
            "opencode model flag must be exactly two argv tokens "
            f"('-m <value>' or '--model <value>'); got {parts!r} from {model_flag!r}"
        )
    if parts[1].startswith("-"):
        raise ValueError(
            "opencode model value must not itself start with '-' "
            f"(flag-injection guard); got value={parts[1]!r}"
        )
    return parts


def _tokenize_pi_model_flag(model_flag: str) -> list[str]:
    parts = shlex.split(model_flag)
    if len(parts) != _PI_MODEL_FLAG_PARTS:
        raise ValueError(
            "pi --model flag must be exactly two argv tokens "
            f"('--model <value>'); got {len(parts)} tokens from "
            f"{model_flag!r}"
        )
    if parts[0] != "--model":
        raise ValueError(
            "pi model_flag must start with the literal '--model' flag "
            f"name; got first token {parts[0]!r}"
        )
    if parts[1].startswith("-"):
        raise ValueError(
            "pi --model value must not itself start with '-' "
            "(flag-injection guard); got "
            f"value={parts[1]!r}"
        )
    return parts


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

    cmd.extend(
        [
            "--mcp-config",
            claude_mcp_config(
                build_options.mcp_endpoint,
                workspace_path=build_options.workspace_path,
                unsafe_mode=build_options.unsafe_mode,
            ),
        ]
    )
    if build_options.allowed_mcp_tool_names:
        cmd.extend(
            [
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
    if transport == AgentTransport.GENERIC and build_options.master_prompt_file:
        cmd.append(
            _materialize_generic_prompt_with_master(
                prompt_file,
                build_options.workspace_path,
                build_options.master_prompt_file,
            )
        )
        return
    cmd.append(prompt_file)


@runtime_checkable
class CommandBuilder(Protocol):
    def build(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: _BuildCommandOptions,
    ) -> list[str]: ...


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
    cmd_argv_override: bool = False
    yolo_before_session: bool = False
    workspace_dir_flag: tuple[str, ...] | None = None
    honors_output_flag: bool = True


class ConfigurableCommandBuilder:
    def __init__(self, spec: CommandBuilderSpec) -> None:
        self.spec = spec

    def _init_cmd(self, config: AgentConfig) -> list[str]:
        cmd = list(self.spec.base_argv)
        if not cmd:
            return []
        if self.spec.cmd_argv_override:
            return shlex.split(config.cmd)
        return [_agent_command_name(config), *cmd[1:]]

    def _build_yolo_session_flags(
        self,
        config: AgentConfig,
        options: _BuildCommandOptions,
    ) -> list[str]:
        flags: list[str] = []
        yolo = config.yolo_flag if config.yolo_flag is not None else self.spec.yolo_flag

        if self.spec.yolo_before_session:
            if yolo is not None:
                flags.extend(_split_optional_flag(yolo))
            flags.extend(_format_session_flag(config.session_flag, options.session_id))
        else:
            flags.extend(_format_session_flag(config.session_flag, options.session_id))
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
                return _tokenize_opencode_model_flag(effective_model)
            formatted = self.spec.model_flag_template.format(effective_model)
            return formatted.split()
        if "codex" in self.spec.base_argv[0]:
            return shlex.split(effective_model)
        return shlex.split(effective_model)

    def build(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: _BuildCommandOptions,
    ) -> list[str]:
        cmd = self._init_cmd(config)

        if self.spec.format_flag is not None:
            cmd.extend(self.spec.format_flag)

        if self.spec.honors_output_flag:
            output_flag = (
                config.output_flag if config.output_flag is not None else self.spec.output_flag
            )
            if output_flag is not None:
                cmd.extend(_split_optional_flag(output_flag))

        cmd.extend(self._build_yolo_session_flags(config, options))

        if options.workspace_path is not None and self.spec.workspace_dir_flag is not None:
            cmd.extend([*self.spec.workspace_dir_flag, str(options.workspace_path)])

        if options.verbose and config.verbose_flag:
            cmd.append(config.verbose_flag)

        cmd.extend(self._build_model_flag(config, options))

        if self.spec.extra_flags_before_prompt:
            cmd.extend(self.spec.extra_flags_before_prompt)

        print_flag = config.print_flag if config.print_flag is not None else self.spec.print_flag
        if print_flag is not None:
            cmd.append(print_flag)

        if self.spec.positional_prompt:
            transport = _agent_transport(config)
            master_prompt_file = (
                None if transport == AgentTransport.CODEX else options.master_prompt_file
            )
            prompt_text = _load_prompt_with_master(
                prompt_file,
                options.workspace_path,
                master_prompt_file,
            )
            cmd.append(prompt_text)

        return cmd


class OpencodeCommandBuilder(ConfigurableCommandBuilder):
    SPEC = CommandBuilderSpec(
        base_argv=("opencode", "run"),
        format_flag=("--format", "json"),
        yolo_flag="--auto",
        output_flag=None,
        honors_output_flag=False,
        model_flag_template="--model {}",
        positional_prompt=True,
        print_flag=None,
        extra_flags_before_prompt=(),
    )

    def __init__(self) -> None:
        super().__init__(self.SPEC)


class NanocoderCommandBuilder(ConfigurableCommandBuilder):
    SPEC = CommandBuilderSpec(
        base_argv=("nanocoder",),
        format_flag=None,
        output_flag=None,
        yolo_flag="--mode yolo",
        model_flag_template=None,
        positional_prompt=True,
        print_flag=None,
        extra_flags_before_prompt=("--no-plain", "run"),
    )

    def __init__(self) -> None:
        super().__init__(self.SPEC)


class CodexCommandBuilder(ConfigurableCommandBuilder):
    SPEC = CommandBuilderSpec(
        base_argv=("codex", "exec"),
        format_flag=None,
        output_flag="--json",
        yolo_flag="--dangerously-bypass-approvals-and-sandbox",
        model_flag_template=None,
        positional_prompt=True,
        print_flag=None,
        extra_flags_before_prompt=(),
        cmd_argv_override=True,
    )

    def __init__(self) -> None:
        super().__init__(self.SPEC)


class ClaudeInteractiveCommandBuilder:
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
            cmd.extend(_format_session_flag(config.session_flag, options.session_id))
        elif options.initial_session_id:
            cmd.extend(["--session-id", options.initial_session_id])
        if options.settings_json is not None:
            cmd.extend(["--settings", options.settings_json])
        if options.master_prompt_file:
            cmd.extend(["--append-system-prompt-file", options.master_prompt_file])
        effective_model = options.model_flag or config.model_flag
        if effective_model:
            cmd.extend(shlex.split(effective_model))
        _append_transport_prompt_arg(cmd, AgentTransport.CLAUDE_INTERACTIVE, prompt_file, options)
        return cmd


class AgyCommandBuilder(ConfigurableCommandBuilder):
    SPEC = CommandBuilderSpec(
        base_argv=("agy",),
        format_flag=None,
        output_flag="--output-format stream-json",
        yolo_flag="--dangerously-skip-permissions",
        model_flag_template=None,
        positional_prompt=True,
        print_flag="--print",
        extra_flags_before_prompt=("--print-timeout", "2h"),
        cmd_argv_override=True,
        yolo_before_session=True,
        workspace_dir_flag=("--add-dir",),
    )

    def __init__(self) -> None:
        super().__init__(self.SPEC)


class CursorCommandBuilder(ConfigurableCommandBuilder):
    SPEC = CommandBuilderSpec(
        base_argv=("agent",),
        format_flag=None,
        output_flag="--output-format stream-json",
        yolo_flag="--yolo",
        model_flag_template="--model {}",
        positional_prompt=True,
        print_flag="--print",
        extra_flags_before_prompt=("--trust", "--approve-mcps"),
        cmd_argv_override=True,
    )

    def __init__(self) -> None:
        super().__init__(self.SPEC)


class PiCommandBuilder(ConfigurableCommandBuilder):
    SPEC = CommandBuilderSpec(
        base_argv=("pi",),
        format_flag=None,
        output_flag="--mode json",
        yolo_flag="--approve",
        model_flag_template="--model {}",
        positional_prompt=True,
        print_flag=None,
        extra_flags_before_prompt=(),
        cmd_argv_override=True,
    )

    def __init__(self) -> None:
        super().__init__(self.SPEC)

    def build(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: _BuildCommandOptions,
    ) -> list[str]:
        cmd = self._init_cmd(config)
        if self.spec.output_flag is not None:
            cmd.extend(_split_optional_flag(self.spec.output_flag))

        extension_path = options.pi_mcp_extension_path
        if extension_path is None and options.mcp_endpoint and options.workspace_path is not None:
            extension_path = str(pi_mcp_extension_path(options.workspace_path))
        if extension_path is not None:
            cmd.extend(["--extension", extension_path])

        cmd.extend(self._build_yolo_session_flags(config, options))
        if options.verbose and config.verbose_flag:
            cmd.append(config.verbose_flag)
        cmd.extend(self._build_model_flag(config, options))
        prompt_text = _load_prompt_with_master(
            prompt_file,
            options.workspace_path,
            options.master_prompt_file,
        )
        cmd.append(prompt_text)
        return cmd


class KimiCommandBuilder(ConfigurableCommandBuilder):
    SPEC = CommandBuilderSpec(
        base_argv=("kimi",),
        format_flag=None,
        output_flag="--output-format=stream-json",
        yolo_flag=None,
        model_flag_template="-m {}",
        positional_prompt=True,
        print_flag="-p",
        extra_flags_before_prompt=(),
        cmd_argv_override=True,
    )

    def __init__(self) -> None:
        super().__init__(self.SPEC)


class DefaultCommandBuilder:
    def build(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: _BuildCommandOptions,
    ) -> list[str]:
        cmd = shlex.split(config.cmd)
        transport = _agent_transport(config)

        if transport == AgentTransport.CLAUDE and config.output_flag is not None:
            cmd.append(config.output_flag)
            if (
                _agent_command_name(config) == "claude"
                and config.output_flag == "--output-format=stream-json"
                and config.verbose_flag
                and config.verbose_flag not in cmd
            ):
                cmd.append(config.verbose_flag)

        if config.print_flag and not _command_already_enables_print_mode(cmd):
            cmd.append(config.print_flag)

        if config.streaming_flag:
            cmd.append(config.streaming_flag)

        if config.session_flag and options.session_id:
            cmd.extend(_format_session_flag(config.session_flag, options.session_id))

        cmd.extend(_split_optional_flag(config.yolo_flag))

        if options.verbose and config.verbose_flag and config.verbose_flag not in cmd:
            cmd.append(config.verbose_flag)

        _extend_claude_transport_flags(cmd, transport, options)

        if transport == AgentTransport.CLAUDE and options.master_prompt_file:
            cmd.extend(["--append-system-prompt-file", options.master_prompt_file])

        effective_model = options.model_flag or config.model_flag
        if effective_model:
            cmd.extend(shlex.split(effective_model))

        _append_transport_prompt_arg(cmd, transport, prompt_file, options)
        return cmd


COMMAND_BUILDERS: dict[AgentTransport, type[CommandBuilder]] = {
    AgentTransport.OPENCODE: OpencodeCommandBuilder,
    AgentTransport.NANOCODER: NanocoderCommandBuilder,
    AgentTransport.CODEX: CodexCommandBuilder,
    AgentTransport.CLAUDE_INTERACTIVE: ClaudeInteractiveCommandBuilder,
    AgentTransport.AGY: AgyCommandBuilder,
    AgentTransport.PI: PiCommandBuilder,
    AgentTransport.CURSOR: CursorCommandBuilder,
    AgentTransport.KIMI: KimiCommandBuilder,
    AgentTransport.CLAUDE: DefaultCommandBuilder,
    AgentTransport.GENERIC: DefaultCommandBuilder,
}

__all__ = [
    "COMMAND_BUILDERS",
    "AgyCommandBuilder",
    "ClaudeInteractiveCommandBuilder",
    "CodexCommandBuilder",
    "CommandBuilder",
    "CursorCommandBuilder",
    "DefaultCommandBuilder",
    "KimiCommandBuilder",
    "NanocoderCommandBuilder",
    "OpencodeCommandBuilder",
    "PiCommandBuilder",
]
