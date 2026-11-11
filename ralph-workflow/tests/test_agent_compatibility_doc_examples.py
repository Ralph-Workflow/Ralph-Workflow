from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from ralph.agents.builtin import builtin_supports
from ralph.config.agent_config import AgentConfig
from ralph.config.ccs_config import CcsConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = REPO_ROOT / "docs" / "sphinx" / "agent-compatibility.md"
TROUBLESHOOTING_PATH = REPO_ROOT / "docs" / "sphinx" / "troubleshooting.md"
AGENTS_PATH = REPO_ROOT / "docs" / "sphinx" / "agents.md"


def _extract_toml_blocks(markdown: str) -> dict[str, str]:
    pattern = re.compile(r"```toml\s*\n(.*?)\n```", re.DOTALL)
    blocks: dict[str, str] = {}
    for match in pattern.finditer(markdown):
        toml_text = match.group(1)
        headers = re.findall(r"^\s*(\[[^\]]+\])", toml_text, re.MULTILINE)
        for header in headers:
            table_name = header.strip()
            blocks[table_name] = toml_text
    return blocks


def test_doc_examples_match_real_agentconfig_schema() -> None:
    markdown = DOC_PATH.read_text(encoding="utf-8")
    blocks = _extract_toml_blocks(markdown)
    agent_blocks = {
        name: toml_text for name, toml_text in blocks.items() if name.startswith("[agents.")
    }
    assert agent_blocks, "expected at least one [agents.*] TOML example in agent-compatibility.md"

    allowed_fields = set(AgentConfig.model_fields.keys()) | {"__doc_skip__"}
    required_fields = {"cmd"}

    for table_name, toml_text in agent_blocks.items():
        try:
            data: dict[str, Any] = tomllib.loads(toml_text)
        except tomllib.TOMLDecodeError as exc:
            pytest.fail(f"{table_name} TOML failed to parse: {exc}")
        assert data, f"{table_name} parsed to an empty table"

        agent_dict = (
            data.get("agents", {}).get(table_name.removeprefix("[agents.").removesuffix("]"))
            if "agents" in data
            else data
        )
        if not agent_dict:
            inner_key = table_name.removeprefix("[agents.").removesuffix("]")
            agent_dict = data.get(inner_key, data)

        forbidden_seen = sorted(
            field for field in ("name", "command", "args") if field in agent_dict
        )
        assert not forbidden_seen, (
            f"{table_name} declares fields that AgentConfig does not accept "
            f"(forbidden: {forbidden_seen}); AgentConfig uses ``cmd`` (not "
            f"``command``) and has no ``name``/``args`` field."
        )

        missing_required = sorted(required_fields - agent_dict.keys())
        assert not missing_required, (
            f"{table_name} is missing the required AgentConfig field(s) "
            f"{missing_required}; AgentConfig requires ``cmd``."
        )

        unexpected = sorted(set(agent_dict.keys()) - allowed_fields)
        assert not unexpected, (
            f"{table_name} declares field(s) {unexpected} that AgentConfig "
            f"does not accept. Allowed: {sorted(allowed_fields)}."
        )

        AgentConfig.model_validate(agent_dict)


def test_doc_examples_match_builtin_flag_inventory() -> None:
    markdown = DOC_PATH.read_text(encoding="utf-8")
    blocks = _extract_toml_blocks(markdown)

    doc_table_for = {
        "claude": "[agents.claude]",
        "claude-headless": "[agents.claude-headless]",
        "codex": "[agents.codex]",
        "opencode": "[agents.opencode]",
        "nanocoder": "[agents.nanocoder]",
        "agy": "[agents.agy]",
        "pi": "[agents.pi]",
        "cursor": "[agents.cursor]",
    }

    builtins = {support.name: support for support in builtin_supports()}

    for agent_name, table_header in doc_table_for.items():
        if table_header not in blocks:
            pytest.fail(
                f"agent-compatibility.md is missing a documented TOML example "
                f"for built-in {agent_name!r} (expected {table_header})"
            )
        support = builtins.get(agent_name)
        assert support is not None, f"built-in {agent_name!r} missing from registry"

        raw = blocks[table_header]
        cleaned = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))
        data: dict[str, Any] = tomllib.loads(cleaned)
        agent_dict = (
            data.get("agents", {}).get(agent_name)
            if "agents" in data
            else data.get(agent_name, data)
        )

        declared_flags = {
            k: v
            for k, v in agent_dict.items()
            if k
            in {
                "cmd",
                "output_flag",
                "yolo_flag",
                "print_flag",
                "verbose_flag",
                "streaming_flag",
                "session_flag",
                "json_parser",
            }
        }
        for flag, doc_value in declared_flags.items():
            builtin_value = getattr(support.config, flag, None)
            assert doc_value == builtin_value, (
                f"{table_header} declares {flag} = {doc_value!r} but "
                f"the built-in {agent_name!r} registry in "
                f"ralph/agents/builtin.py has {flag} = {builtin_value!r}. "
                f"The doc and the built-in registry must agree."
            )


def test_ccs_block_matches_real_ccsconfig_schema() -> None:
    markdown = DOC_PATH.read_text(encoding="utf-8")
    blocks = _extract_toml_blocks(markdown)
    ccs_block = blocks.get("[ccs]")
    assert ccs_block is not None, "agent-compatibility.md is missing the [ccs] TOML example"

    cleaned = "\n".join(
        line for line in ccs_block.splitlines() if not line.lstrip().startswith("#")
    )
    data: dict[str, Any] = tomllib.loads(cleaned)
    ccs_dict = data.get("ccs", data)

    allowed_fields = set(CcsConfig.model_fields.keys())
    unexpected = sorted(set(ccs_dict.keys()) - allowed_fields)
    assert not unexpected, (
        f"[ccs] block declares field(s) {unexpected} that CcsConfig does not "
        f"accept. Allowed: {sorted(allowed_fields)}."
    )

    CcsConfig.model_validate(ccs_dict)

    if "yolo_flag" in ccs_dict:
        assert ccs_dict["yolo_flag"] == "--permission-mode bypassPermissions", (
            f"[ccs] yolo_flag must be '--permission-mode bypassPermissions' (the headless "
            f"Claude path), not {ccs_dict['yolo_flag']!r}."
        )


def test_cursor_credential_prose_documents_file_auth_and_keychain_policy() -> None:
    docs = (
        DOC_PATH.read_text(encoding="utf-8"),
        TROUBLESHOOTING_PATH.read_text(encoding="utf-8"),
        AGENTS_PATH.read_text(encoding="utf-8"),
    )

    for markdown in docs:
        assert "AGENT_CLI_CREDENTIAL_STORE" in markdown
        assert "file-backed" in markdown
        assert "Keychain" in markdown
        assert "macOS" in markdown
        assert "Linux" in markdown
        assert "~/.cursor/auth.json" in markdown
        assert "~/.config/cursor/auth.json" in markdown
        assert "CURSOR_AUTH_TOKEN" not in markdown


def test_doc_does_not_claim_fabricated_flags() -> None:
    markdown = DOC_PATH.read_text(encoding="utf-8")
    prose_only = re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)
    assert "--full-auto" not in prose_only, (
        "agent-compatibility.md still references the fabricated --full-auto "
        "flag in its prose; this was the exact bug the analysis feedback "
        "flagged."
    )
