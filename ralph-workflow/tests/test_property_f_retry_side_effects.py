# property-test: F — retried tool calls have defined, observable side-effect semantics
"""Retries are safe because their effect on the world is defined.

A tool call that is retried after a committed-then-failed response has
explicitly defined side-effect semantics. A command that may have partially
executed before a stream failed is surfaced as ``partial: True`` and the
recovery controller refuses to re-execute.

The default-deny test asserts the static registry covers every tool in
``RalphToolName`` — a new tool without a contract is a test failure, the
gate that keeps the contract closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.tools._side_effects import (
    REGISTRY,
    SideEffectContract,
    get_contract,
)
from ralph.mcp.tools.names import RalphToolName


def test_side_effect_registry_covers_every_ralph_tool() -> None:
    """Every tool in RalphToolName has a side-effect contract.

    This is the default-deny test: a new tool without a classification is
    a test failure. The recovery controller refuses to re-execute
    ``unknown`` contracts, so an unclassified tool silently loses its
    retry path. The closed registry forces a conscious choice.
    """
    missing = sorted(
        member.value for member in RalphToolName if member.value not in REGISTRY
    )
    assert not missing, f"RalphToolName members missing side-effect contract: {missing}"


def test_exec_is_classified_as_mutate() -> None:
    """exec is mutate, idempotent=False — a partial exec is unsafe to re-run."""
    contract = get_contract("exec")
    assert contract.classification == "mutate"
    assert contract.idempotent is False


def test_unsafe_exec_is_classified_as_mutate() -> None:
    """unsafe_exec is mutate, idempotent=False."""
    contract = get_contract("unsafe_exec")
    assert contract.classification == "mutate"
    assert contract.idempotent is False


def test_read_file_is_classified_as_read() -> None:
    """read_file is read, idempotent=True — a partial read may be re-run."""
    contract = get_contract("read_file")
    assert contract.classification == "read"
    assert contract.idempotent is True


def test_write_file_is_classified_as_mutate() -> None:
    """write_file is mutate — a partial write is unsafe to re-run."""
    contract = get_contract("write_file")
    assert contract.classification == "mutate"
    assert contract.idempotent is False


def test_web_search_is_classified_as_mutate() -> None:
    """web_search is mutate (may set cookies) — no silent re-execute."""
    contract = get_contract("web_search")
    assert contract.classification == "mutate"
    assert contract.idempotent is False


def test_unknown_tool_returns_unknown_contract() -> None:
    """An unknown tool name returns an unknown, non-idempotent contract (safe default)."""
    contract = get_contract("nonexistent_tool_xyz")
    assert contract.classification == "unknown"
    assert contract.idempotent is False


def test_get_contract_returns_side_effect_contract_dataclass() -> None:
    """get_contract returns a frozen SideEffectContract dataclass."""
    contract = get_contract("read_file")
    assert isinstance(contract, SideEffectContract)
    assert contract.tool_name == "read_file"


def test_read_tools_have_idempotent_true() -> None:
    """All read-classified tools in REGISTRY are idempotent."""
    for name, contract in REGISTRY.items():
        if contract.classification == "read":
            assert contract.idempotent is True, (
                f"read-classified tool {name!r} must be idempotent"
            )


def test_mutate_tools_have_idempotent_false() -> None:
    """All mutate-classified tools in REGISTRY are not idempotent."""
    for name, contract in REGISTRY.items():
        if contract.classification == "mutate":
            assert contract.idempotent is False, (
                f"mutate-classified tool {name!r} must NOT be idempotent"
            )


def test_side_effects_module_is_documented_and_pure() -> None:
    """The _side_effects module is a pure dataclass registry (no IO)."""
    text = (
        Path(__file__).parent.parent
        / "ralph"
        / "mcp"
        / "tools"
        / "_side_effects.py"
    ).read_text()
    # No subprocess / network / time / file imports
    for forbidden in ("subprocess", "urllib", "httpx", "requests", "time.sleep", "open("):
        assert forbidden not in text, (
            f"_side_effects.py must remain pure; found {forbidden!r}"
        )


@pytest.mark.parametrize(
    "tool_name,expected",
    [
        ("exec", "mutate"),
        ("unsafe_exec", "mutate"),
        ("raw_exec", "mutate"),
        ("write_file", "mutate"),
        ("edit_file", "mutate"),
        ("delete_path", "mutate"),
        ("read_file", "read"),
        ("list_directory", "read"),
        ("search_files", "read"),
        ("git_status", "read"),
        ("git_log", "read"),
        ("read_image", "read"),
    ],
)
def test_specific_tool_classifications(tool_name: str, expected: str) -> None:
    """Parametrized: each named tool has the expected side-effect classification."""
    contract = get_contract(tool_name)
    assert contract.classification == expected
