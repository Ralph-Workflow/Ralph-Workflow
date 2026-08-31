"""Tests for the agent registry."""

from __future__ import annotations

import pytest

from ralph.agents.invoke import BuildCommandOptions
from ralph.agents.invoke._command_builders import (
    ClaudeInteractiveCommandBuilder,
    DefaultCommandBuilder,
)
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, CcsAliasConfig, UnifiedConfig


def test_agent_registry_registers_and_resolves_agents() -> None:
    registry = AgentRegistry()
    claude = AgentConfig(cmd="claude", output_flag="--json-stream")

    registry.register("claude", claude)

    assert registry.get("claude") == claude
    assert registry.get("missing") is None
    assert registry.list_agents() == ["claude"]
    assert registry.get_command("claude") == "claude"
    assert registry.get_command("missing") is None


def test_agent_registry_from_config_loads_all_agents() -> None:
    config = UnifiedConfig(
        agents={
            "claude": AgentConfig(cmd="claude"),
            "opencode": AgentConfig(cmd="opencode", can_commit=True),
        }
    )

    registry = AgentRegistry.from_config(config)

    assert set(registry.list_agents()) >= {
        "claude",
        "claude-headless",
        "codex",
        "opencode",
        "agy",
        "nanocoder",
    }
    assert registry.get("opencode") == AgentConfig(cmd="opencode", can_commit=True)


def test_builtin_claude_agent_is_claude_interactive_transport() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    claude = registry.get("claude")

    assert claude is not None
    assert claude.cmd == "claude"
    assert claude.transport == AgentTransport.CLAUDE_INTERACTIVE


def test_builtin_claude_headless_agent_is_claude_transport() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    claude_headless = registry.get("claude-headless")

    assert claude_headless is not None
    assert claude_headless.cmd == "claude -p"
    assert claude_headless.transport == AgentTransport.CLAUDE
    assert claude_headless.output_flag == "--output-format=stream-json"


def test_agent_registry_validate_reports_missing_required_fields() -> None:
    registry = AgentRegistry()
    registry.register(
        "missing-cmd", AgentConfig.model_construct(cmd="", output_flag="--json-stream")
    )
    registry.register(
        "missing-output",
        AgentConfig.model_construct(
            cmd="claude -p",
            output_flag="",
            transport=AgentTransport.CLAUDE,
        ),
    )

    assert registry.validate() == [
        "Agent 'missing-cmd' has no command configured",
        "Agent 'missing-output' has no output flag configured",
    ]


def test_agent_registry_from_config_includes_builtin_agents() -> None:
    config = UnifiedConfig()

    registry = AgentRegistry.from_config(config)

    claude = registry.get("claude")
    codex = registry.get("codex")
    opencode = registry.get("opencode")

    assert claude is not None
    assert codex is not None
    assert opencode is not None
    assert claude.cmd == "claude"
    assert claude.yolo_flag == "--dangerously-skip-permissions"
    assert claude.transport == AgentTransport.CLAUDE_INTERACTIVE
    claude_headless = registry.get("claude-headless")
    assert claude_headless is not None
    assert claude_headless.cmd == "claude -p"
    assert claude_headless.transport == AgentTransport.CLAUDE
    assert codex.cmd == "codex exec"
    assert codex.output_flag == "--json"
    assert codex.yolo_flag == "--dangerously-bypass-approvals-and-sandbox"
    assert codex.transport == AgentTransport.CODEX
    # OpenCode used to be the only agent with no permission flag, so in an
    # unattended run it auto-REJECTED any permission request it could not
    # match. "--auto" approves what is not explicitly denied, so an
    # operator's own denies still win.
    assert opencode.yolo_flag == "--auto"
    assert opencode.transport == AgentTransport.OPENCODE

    agy = registry.get("agy")
    assert agy is not None
    assert agy.cmd == "agy"
    assert agy.transport == AgentTransport.AGY
    assert agy.yolo_flag == "--dangerously-skip-permissions"
    assert agy.print_flag == "--print"
    assert agy.session_flag is None

    nanocoder = registry.get("nanocoder")
    assert nanocoder is not None
    assert nanocoder.cmd == "nanocoder"
    assert nanocoder.transport == AgentTransport.NANOCODER
    assert nanocoder.can_commit is False
    assert nanocoder.session_flag is None


def test_ccs_alias_keeps_claude_transport() -> None:
    config = UnifiedConfig(ccs_aliases={"glm": "ccs glm"})

    registry = AgentRegistry.from_config(config)
    ccs_agent = registry.get("ccs/glm")

    assert ccs_agent is not None
    assert ccs_agent.cmd == "ccs glm"
    assert ccs_agent.output_flag == config.ccs.output_flag
    assert ccs_agent.yolo_flag == "--permission-mode bypassPermissions"
    assert ccs_agent.print_flag == config.ccs.print_flag
    assert ccs_agent.streaming_flag == config.ccs.streaming_flag
    assert ccs_agent.transport == AgentTransport.CLAUDE


def test_agent_registry_resolves_string_ccs_alias_with_defaults() -> None:
    config = UnifiedConfig(ccs_aliases={"glm": "ccs glm"})

    registry = AgentRegistry.from_config(config)
    ccs_agent = registry.get("ccs/glm")

    assert ccs_agent is not None
    assert ccs_agent.cmd == "ccs glm"
    assert ccs_agent.output_flag == config.ccs.output_flag
    assert ccs_agent.yolo_flag == "--permission-mode bypassPermissions"
    assert ccs_agent.print_flag == config.ccs.print_flag
    assert ccs_agent.streaming_flag == config.ccs.streaming_flag
    assert ccs_agent.transport == AgentTransport.CLAUDE


def test_agent_registry_resolves_table_ccs_alias_with_overrides() -> None:
    config = UnifiedConfig(
        ccs_aliases={
            "work": CcsAliasConfig(
                cmd="ccs work",
                output_flag="--json-stream",
                verbose_flag="--vv",
                model_flag="--model custom",
                can_commit=False,
            )
        }
    )

    registry = AgentRegistry.from_config(config)
    ccs_agent = registry.get("ccs/work")

    assert ccs_agent is not None
    assert ccs_agent.cmd == "ccs work"
    assert ccs_agent.output_flag == "--json-stream"
    assert ccs_agent.verbose_flag == "--vv"
    assert ccs_agent.model_flag == "--model custom"
    assert ccs_agent.can_commit is False
    assert ccs_agent.transport == AgentTransport.CLAUDE


def test_agent_registry_resolves_direct_opencode_model_reference() -> None:
    """A dynamic ``opencode/<provider>/<model>`` alias resolves to the built-in.

    ``output_flag`` is asserted to be ``None``: it used to carry
    ``--json-stream``, a flag opencode 1.18.25 does not have. It never
    reached the command line (the builder dropped it), so the constant was
    dead. The one output selector Ralph passes is ``--format json``, which
    the command builder emits unconditionally for this transport.
    """
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("opencode/minimax/MiniMax-M2.7-highspeed")

    assert agent is not None
    assert agent.cmd == "opencode"
    assert agent.output_flag is None
    assert agent.json_parser == "opencode"
    assert agent.model_flag == "-m minimax/MiniMax-M2.7-highspeed"
    assert agent.can_commit is True


def test_claude_model_reference_resolves_to_claude_interactive() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("claude/opus")

    assert agent is not None
    assert agent.cmd == "claude"
    assert agent.output_flag is None
    assert agent.json_parser == "claude"
    assert agent.transport == AgentTransport.CLAUDE_INTERACTIVE
    assert agent.model_flag == "--model opus"
    assert agent.can_commit is True


def test_agent_registry_resolves_direct_claude_model_reference() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("claude/opus")

    assert agent is not None
    assert agent.cmd == "claude"
    assert agent.output_flag is None
    assert agent.json_parser == "claude"
    assert agent.transport == AgentTransport.CLAUDE_INTERACTIVE
    assert agent.model_flag == "--model opus"
    assert agent.can_commit is True


def test_claude_haiku_alias_builds_interactive_argv_end_to_end() -> None:
    """``claude/haiku`` flows from the registry to a correct PTY argv.

    Regression pin for the dynamic-alias → command-builder pipeline on
    the interactive transport: the built argv must contain the claude
    PTY binary, the yolo flag, and ``--model haiku`` as two clean
    consecutive argv tokens.
    """
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("claude/haiku")

    assert agent is not None
    assert agent.transport == AgentTransport.CLAUDE_INTERACTIVE
    argv = ClaudeInteractiveCommandBuilder().build(
        agent, "PROMPT.md", options=BuildCommandOptions()
    )
    assert argv[0] == "claude"
    assert "--dangerously-skip-permissions" in argv
    model_index = argv.index("--model")
    assert argv[model_index + 1] == "haiku"


@pytest.mark.parametrize("alias", ["haiku", "sonnet", "opus"])
def test_claude_alias_argv_end_to_end(alias: str) -> None:
    """``claude/<alias>`` builds correct interactive and headless argv.

    Covers all three Anthropic short aliases end to end: the dynamic-alias
    resolver must synthesize ``model_flag == "--model <alias>"`` on the
    interactive transport with the yolo flag in the built PTY argv, and the
    headless transport must agree -- ``--model`` followed by the bare alias
    as two consecutive argv tokens. A regression that flips an alias's
    transport or strips the yolo flag surfaces here instead of as a silent
    CLI failure.
    """
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get(f"claude/{alias}")

    assert agent is not None
    assert agent.model_flag == f"--model {alias}"
    assert agent.transport == AgentTransport.CLAUDE_INTERACTIVE
    argv = ClaudeInteractiveCommandBuilder().build(
        agent, "PROMPT.md", options=BuildCommandOptions()
    )
    assert "--dangerously-skip-permissions" in argv

    headless = registry.get(f"claude-headless/{alias}")

    assert headless is not None
    headless_argv = DefaultCommandBuilder().build(
        headless, "PROMPT.md", options=BuildCommandOptions()
    )
    assert headless_argv[headless_argv.index("--model") + 1] == alias


def test_claude_whitespace_model_id_survives_as_single_argv_token() -> None:
    """A model id containing whitespace stays one argv token on both transports.

    Future-proofing pin: the dynamic Claude family resolver shell-quotes
    the model segment and both Claude command builders tokenize with
    ``shlex.split``, so ``claude/<model with space>`` and
    ``claude-headless/<model with space>`` each emit ``--model`` and the
    full id as exactly two consecutive argv tokens instead of splitting
    the id across multiple argv elements.
    """
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("claude/model with space")

    assert agent is not None
    assert agent.model_flag == "--model 'model with space'"
    argv = ClaudeInteractiveCommandBuilder().build(
        agent, "PROMPT.md", options=BuildCommandOptions()
    )
    model_index = argv.index("--model")
    assert argv[model_index + 1] == "model with space"

    headless = registry.get("claude-headless/model with space")

    assert headless is not None
    headless_argv = DefaultCommandBuilder().build(
        headless, "PROMPT.md", options=BuildCommandOptions()
    )
    headless_model_index = headless_argv.index("--model")
    assert headless_argv[headless_model_index + 1] == "model with space"


@pytest.mark.parametrize(
    ("model_id", "expected_model_flag"),
    [
        ("claude-haiku-4-5", "--model claude-haiku-4-5"),
        ("claude-haiku-4-5-20251001", "--model claude-haiku-4-5-20251001"),
        (
            "claude-haiku-4-5[effort=high]",
            "--model 'claude-haiku-4-5[effort=high]'",
        ),
        ("claude-opus-4-6", "--model claude-opus-4-6"),
        ("claude-opus-4-6-20250909", "--model claude-opus-4-6-20250909"),
        (
            "claude-opus-4-6[effort=high]",
            "--model 'claude-opus-4-6[effort=high]'",
        ),
        ("claude-sonnet-4-6", "--model claude-sonnet-4-6"),
        ("claude-sonnet-4-6-20250909", "--model claude-sonnet-4-6-20250909"),
        (
            "claude-sonnet-4-6[effort=high]",
            "--model 'claude-sonnet-4-6[effort=high]'",
        ),
    ],
)
def test_claude_versioned_model_ids_stay_single_argv_tokens(
    model_id: str, expected_model_flag: str
) -> None:
    """Anthropic full-id and bracketed-effort forms resolve to one argv token.

    Every documented Anthropic model family (Haiku, Sonnet, Opus) is
    pinned with three id-form variants: a short version-segment alias
    (``claude-opus-4-6``), a dating-suffix full id
    (``claude-sonnet-4-6-20250909``), and a bracketed-effort form
    (``claude-opus-4-6[effort=high]``).

    ``claude/<model>`` and ``claude-headless/<model>`` accept versioned
    full ids (``claude-haiku-4-5-20251001``) and bracketed effort
    parameters (``[effort=high]``); the registry shell-quotes the value
    when it contains shell-special characters and both Claude command
    builders emit ``--model`` and the full id as exactly two consecutive
    argv tokens.
    """
    registry = AgentRegistry.from_config(UnifiedConfig())

    interactive = registry.get(f"claude/{model_id}")
    headless = registry.get(f"claude-headless/{model_id}")

    assert interactive is not None
    assert headless is not None
    assert interactive.model_flag == expected_model_flag
    assert headless.model_flag == expected_model_flag

    interactive_argv = ClaudeInteractiveCommandBuilder().build(
        interactive, "PROMPT.md", options=BuildCommandOptions()
    )
    interactive_index = interactive_argv.index("--model")
    assert interactive_argv[interactive_index + 1] == model_id

    headless_argv = DefaultCommandBuilder().build(
        headless, "PROMPT.md", options=BuildCommandOptions()
    )
    headless_index = headless_argv.index("--model")
    assert headless_argv[headless_index + 1] == model_id


def test_agent_config_claude_cmd_infers_claude_interactive() -> None:
    config = AgentConfig(cmd="claude")

    assert config.transport == AgentTransport.CLAUDE_INTERACTIVE


def test_claude_headless_model_reference_resolves() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("claude-headless/haiku")

    assert agent is not None
    assert agent.cmd == "claude -p"
    assert agent.output_flag == "--output-format=stream-json"
    assert agent.transport == AgentTransport.CLAUDE
    assert agent.model_flag == "--model haiku"


@pytest.mark.parametrize("alias", ["haiku", "sonnet", "opus"])
def test_claude_headless_argv_includes_verbose_for_stream_json(alias: str) -> None:
    """Headless Claude stream-json output requires --verbose (Claude CLI 2.1+).

    The command builder must emit --verbose unconditionally for the
    stream-json headless transport so the CLI does not exit with
    ``--output-format=stream-json requires --verbose``.
    """
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get(f"claude-headless/{alias}")
    assert agent is not None

    argv = DefaultCommandBuilder().build(
        agent, "tmp/headless-claude-smoke/PROMPT.md", options=BuildCommandOptions()
    )

    assert "--verbose" in argv
    # --verbose should appear exactly once even when options.verbose is False.
    assert argv.count("--verbose") == 1


@pytest.mark.parametrize(
    ("transport", "prefix"),
    [
        (AgentTransport.CLAUDE_INTERACTIVE, "claude"),
        (AgentTransport.CLAUDE, "claude-headless"),
    ],
)
@pytest.mark.parametrize("alias", ["haiku", "sonnet", "opus"])
def test_claude_dynamic_alias_preserves_model_field(
    transport: AgentTransport, prefix: str, alias: str
) -> None:
    """S-3 regression: dynamic aliases retain retry-model provenance."""
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get(f"{prefix}/{alias}")

    assert agent is not None
    assert agent.transport == transport
    assert agent.model == alias


def test_registry_validate_exempts_claude_interactive_output_flag() -> None:
    registry = AgentRegistry()
    registry.register(
        "interactive",
        AgentConfig(
            cmd="claude",
            output_flag=None,
            transport=AgentTransport.CLAUDE_INTERACTIVE,
        ),
    )

    assert registry.validate() == []


def test_agent_registry_resolves_direct_ccs_model_reference() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("ccs/mm")

    assert agent is not None
    assert agent.cmd == "ccs mm"
    assert agent.output_flag == "--output-format=stream-json"
    assert agent.yolo_flag == "--permission-mode bypassPermissions"
    assert agent.verbose_flag == "--verbose"
    assert agent.json_parser == "claude"
    assert agent.transport == AgentTransport.CLAUDE
    assert agent.print_flag == "--print"
    assert agent.streaming_flag == "--include-partial-messages"
    assert agent.session_flag == "--resume {}"
    assert agent.can_commit is True


def test_agent_registry_resolves_two_segment_opencode_model_reference() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("opencode/MiniMax-M2.7-highspeed")

    assert agent is not None
    assert agent.cmd == "opencode"
    assert agent.transport == AgentTransport.OPENCODE
    assert agent.model_flag == "-m MiniMax-M2.7-highspeed"
    assert agent.can_commit is True


def test_agent_registry_resolves_direct_nanocoder_provider_model_reference() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("nanocoder/ollama/llama3.1")

    assert agent is not None
    assert agent.cmd == "nanocoder"
    assert agent.transport == AgentTransport.NANOCODER
    assert agent.model_flag == "--provider ollama --model llama3.1"
    assert agent.can_commit is True


def test_agent_registry_resolves_direct_nanocoder_provider_reference() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("nanocoder/minimax")

    assert agent is not None
    assert agent.cmd == "nanocoder"
    assert agent.transport == AgentTransport.NANOCODER
    assert agent.model_flag == "--provider minimax"
    assert agent.can_commit is True


def test_agent_registry_resolves_pi_provider_model_path_reference() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("pi/anthropic/claude-sonnet-4-20250514/latest:high")

    assert agent is not None
    assert agent.cmd == "pi"
    assert agent.transport == AgentTransport.PI
    assert agent.model_flag == "--model anthropic/claude-sonnet-4-20250514/latest:high"
    assert agent.can_commit is True


@pytest.mark.parametrize(
    "name",
    [
        "opencode/",
        "opencode//model",
        "nanocoder/",
        "nanocoder//model",
        "nanocoder/provider/",
        "nanocoder//",
        "claude/",
        "claude//model",
    ],
)
def test_agent_registry_rejects_malformed_direct_opencode_reference(name: str) -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    assert registry.get(name) is None
