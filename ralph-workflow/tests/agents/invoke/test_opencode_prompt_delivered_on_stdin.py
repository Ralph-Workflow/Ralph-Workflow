"""OpenCode's prompt must reach the model byte-identically.

`opencode run` re-quotes its positional message: every argv token containing a
space is wrapped in literal double quotes and every `"` inside it is
backslash-escaped, then the tokens are joined and sent as the message text.
Verified against the installed CLI (1.18.25) with a live call --

    argv:   model received  {\\"a\\": \\"b\\"}
    stdin:  model received  {"a": "b"}

Ralph's prompts carry JSON artifact grammars and tool-call examples, so every
OpenCode run was reading a corrupted prompt. The CLI reads the message from
stdin when no positional message is given, which delivers it verbatim.
"""

from __future__ import annotations

from pathlib import Path

from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.agents.invoke._process_reader import split_prompt_for_stdin_delivery
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

_PROMPT = 'Submit the artifact.\nExample: {"type": "plan"}\n'


def _opencode_config() -> AgentConfig:
    return AgentConfig(
        cmd="opencode",
        transport=AgentTransport.OPENCODE,
        session_flag="--session {}",
    )


def test_opencode_regression_prompt_moves_off_argv_onto_stdin() -> None:
    """The composed prompt leaves argv unchanged in content and goes to stdin."""
    argv = ["opencode", "run", "--format", "json", _PROMPT]

    command, stdin_text = split_prompt_for_stdin_delivery(argv, _opencode_config())

    assert command == ["opencode", "run", "--format", "json"]
    assert stdin_text == _PROMPT


def test_opencode_regression_other_transports_keep_their_positional_prompt() -> None:
    """Only OpenCode re-quotes its argv, so only OpenCode changes delivery."""
    argv = ["codex", "exec", "--json", _PROMPT]
    config = AgentConfig(cmd="codex exec", transport=AgentTransport.CODEX)

    command, stdin_text = split_prompt_for_stdin_delivery(argv, config)

    assert command == argv
    assert stdin_text is None


def test_opencode_regression_the_builder_really_puts_the_prompt_last(tmp_path: Path) -> None:
    """Moving the LAST token is only correct because the builder appends it last.

    This pins that coupling rather than assuming it: if the OpenCode spec ever
    stops appending the composed prompt as the final argv token, the split above
    would silently ship a flag value to the model as its prompt.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text(_PROMPT, encoding="utf-8")

    argv = build_command(
        _opencode_config(),
        str(prompt_file),
        options=BuildCommandOptions(session_id="ses_1", workspace_path=tmp_path),
    )

    assert argv[-1].endswith(_PROMPT)
    assert "ses_1" in argv[:-1]
