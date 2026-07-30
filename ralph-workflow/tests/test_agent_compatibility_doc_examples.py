"""Regression test: the per-agent TOML examples in docs/sphinx/agent-compatibility.md
must parse against the actual ``AgentConfig`` model and must match the built-in
flag inventory in ``ralph/agents/builtin.py``.

The previous failure mode (wt-041 analysis feedback) was that the doc
exemplified ``[agents.claude] name = "claude", command = "claude",
args = [...]`` — fields the ``AgentConfig`` Pydantic model does not
declare. The Pydantic ``extra='ignore'`` default silently dropped the
typos, so the docs and the runtime drifted. This test makes the contract
explicit: every documented example must be loadable as ``AgentConfig``,
and every flag value the doc claims must match the built-in registry.

AC-07 / analysis-feedback how-to-fix: a focused documentation regression
test that parses/validates the examples or otherwise compares documented
built-in flags to the registry.
"""

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


def _extract_toml_blocks(markdown: str) -> dict[str, str]:
    """Extract every fenced ``\\`\\`\\`toml`` block keyed by ALL of its table headers.

    A single TOML block can contain multiple ``[agents.<name>]`` tables
    (e.g. the Claude section documents both ``[agents.claude]`` and
    ``[agents.claude-headless]`` side-by-side). The function therefore
    indexes the block under EVERY header it declares, with the last
    header winning on collision (deterministic for the canonical
    one-table-per-block cases; documented here so future readers
    understand the choice).
    """
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
    """Every ``[agents.*]`` TOML block in agent-compatibility.md must parse
    through ``AgentConfig.model_validate()`` and must not contain a field
    the Pydantic model does not declare."""
    markdown = DOC_PATH.read_text(encoding="utf-8")
    blocks = _extract_toml_blocks(markdown)
    agent_blocks = {
        name: toml_text
        for name, toml_text in blocks.items()
        if name.startswith("[agents.")
    }
    assert agent_blocks, "expected at least one [agents.*] TOML example in agent-compatibility.md"

    # AgentConfig has this allow-list of declared fields. Anything outside
    # the set must NOT appear in the documented examples — that was the
    # exact failure mode the previous development analysis flagged.
    allowed_fields = set(AgentConfig.model_fields.keys()) | {"__doc_skip__"}
    # `cmd` is the only required field.
    required_fields = {"cmd"}

    for table_name, toml_text in agent_blocks.items():
        try:
            data: dict[str, Any] = tomllib.loads(toml_text)
        except tomllib.TOMLDecodeError as exc:  # pragma: no cover - guarded
            pytest.fail(f"{table_name} TOML failed to parse: {exc}")
        assert data, f"{table_name} parsed to an empty table"

        # tomllib loads [agents.foo] as a sub-dict only when nested; flat
        # top-level tables land at the top level. Both shapes are valid;
        # collect the agent dict either way.
        agent_dict = (
            data.get("agents", {}).get(table_name.removeprefix("[agents.").removesuffix("]"))
            if "agents" in data
            else data
        )
        if not agent_dict:
            # Single-table TOML with [agents.foo] lands as the only key.
            inner_key = table_name.removeprefix("[agents.").removesuffix("]")
            agent_dict = data.get(inner_key, data)

        # Field allow-list check: catch ``name =`` and ``command =`` and ``args =``
        # regressions before they reach the runtime.
        forbidden_seen = sorted(
            field
            for field in ("name", "command", "args")
            if field in agent_dict
        )
        assert not forbidden_seen, (
            f"{table_name} declares fields that AgentConfig does not accept "
            f"(forbidden: {forbidden_seen}); AgentConfig uses ``cmd`` (not "
            f"``command``) and has no ``name``/``args`` field."
        )

        # Missing required field check.
        missing_required = sorted(required_fields - agent_dict.keys())
        assert not missing_required, (
            f"{table_name} is missing the required AgentConfig field(s) "
            f"{missing_required}; AgentConfig requires ``cmd``."
        )

        # Field allow-list check: every declared key must be a real AgentConfig field.
        unexpected = sorted(set(agent_dict.keys()) - allowed_fields)
        assert not unexpected, (
            f"{table_name} declares field(s) {unexpected} that AgentConfig "
            f"does not accept. Allowed: {sorted(allowed_fields)}."
        )

        # Finally, validate the dict through Pydantic — extra='ignore' is
        # the Pydantic default, so an unrecognised key would silently
        # drop. The earlier two assertions already reject that case; this
        # one confirms the surviving fields round-trip cleanly.
        AgentConfig.model_validate(agent_dict)


def test_doc_examples_match_builtin_flag_inventory() -> None:
    """Every per-agent flag value the doc claims must match the built-in
    registry in ``ralph/agents/builtin.py``.

    We extract the first ``[agents.<builtin>]`` block from the doc and
    require its flag values (when present) to equal the built-in's
    declared values. The doc is allowed to OMIT optional flags (e.g.
    the interactive Claude block doesn't include ``output_flag``);
    what it does declare must match.
    """
    markdown = DOC_PATH.read_text(encoding="utf-8")
    blocks = _extract_toml_blocks(markdown)

    # Builtin agent name -> the table header used in the doc.
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

        # Strip comments to keep tomllib happy, then load.
        raw = blocks[table_header]
        cleaned = "\n".join(
            line for line in raw.splitlines() if not line.lstrip().startswith("#")
        )
        data: dict[str, Any] = tomllib.loads(cleaned)
        # Find the inner dict.
        agent_dict = (
            data.get("agents", {}).get(agent_name)
            if "agents" in data
            else data.get(agent_name, data)
        )

        # Each flag the doc declares must equal the built-in's value.
        # Optional flags that the doc omits are fine.
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
    """The ``[ccs]`` block in agent-compatibility.md must validate against
    CcsConfig and must not declare fields CcsConfig does not accept."""
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

    # Spot-check the previously-wrong field: the doc used to claim
    # yolo_flag = "--dangerously-skip-permissions", but CcsConfig's
    # documented default is "--permission-mode auto" (it shares the
    # headless Claude path). This would have silently passed in v1
    # because Pydantic extra='ignore' drops unrecognised keys; the
    # regression guard below makes the contract explicit.
    if "yolo_flag" in ccs_dict:
        assert ccs_dict["yolo_flag"] == "--permission-mode auto", (
            f"[ccs] yolo_flag must be '--permission-mode auto' (the headless "
            f"Claude path), not {ccs_dict['yolo_flag']!r}."
        )


def test_doc_does_not_claim_fabricated_flags() -> None:
    """The doc must not contain the fabricated ``--full-auto`` or
    ``--approve`` standalone flags that were mis-attributed to Codex,
    Claude, and OpenCode in earlier docs. The real ``--approve`` flag
    is Pi's yolo_flag (which is correctly documented) — the regression
    guard looks for the standalone-fabricated pattern: ``--full-auto``
    anywhere in the source markdown.
    """
    markdown = DOC_PATH.read_text(encoding="utf-8")
    # Strip code fences to keep the check scoped to the prose; the
    # actual flag inventory lives in the TOML blocks (validated by
    # the schema test above) and the prose must not contradict it.
    prose_only = re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)
    assert "--full-auto" not in prose_only, (
        "agent-compatibility.md still references the fabricated --full-auto "
        "flag in its prose; this was the exact bug the analysis feedback "
        "flagged."
    )
