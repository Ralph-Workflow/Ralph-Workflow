from __future__ import annotations

import pytest

from ralph.mcp.tools._side_effects import (
    REGISTRY,
    SideEffectContract,
    get_contract,
    register,
)
from ralph.mcp.tools.names import RalphToolName


def test_side_effect_registry_covers_every_ralph_tool() -> None:
    missing = sorted(member.value for member in RalphToolName if member.value not in REGISTRY)
    assert not missing, f"RalphToolName members missing side-effect contract: {missing}"


def test_exec_is_classified_as_mutate() -> None:
    contract = get_contract("exec")
    assert contract.classification == "mutate"
    assert contract.idempotent is False


def test_unsafe_exec_is_classified_as_mutate() -> None:
    contract = get_contract("unsafe_exec")
    assert contract.classification == "mutate"
    assert contract.idempotent is False


def test_read_file_is_classified_as_read() -> None:
    contract = get_contract("read_file")
    assert contract.classification == "read"
    assert contract.idempotent is True


def test_write_file_is_classified_as_mutate() -> None:
    contract = get_contract("write_file")
    assert contract.classification == "mutate"
    assert contract.idempotent is False


def test_web_search_is_classified_as_mutate() -> None:
    contract = get_contract("web_search")
    assert contract.classification == "mutate"
    assert contract.idempotent is False


def test_unknown_tool_returns_unknown_contract() -> None:
    contract = get_contract("nonexistent_tool_xyz")
    assert contract.classification == "unknown"
    assert contract.idempotent is False


def test_get_contract_returns_side_effect_contract_dataclass() -> None:
    contract = get_contract("read_file")
    assert isinstance(contract, SideEffectContract)
    assert contract.tool_name == "read_file"


def test_read_tools_have_idempotent_true() -> None:
    for name, contract in REGISTRY.items():
        if contract.classification == "read":
            assert contract.idempotent is True, f"read-classified tool {name!r} must be idempotent"


def test_mutate_tools_have_idempotent_false() -> None:
    for name, contract in REGISTRY.items():
        if contract.classification == "mutate":
            assert contract.idempotent is False, (
                f"mutate-classified tool {name!r} must NOT be idempotent"
            )


def test_registry_purity_observable_via_behavior() -> None:
    register("test_purity_tool", "read")
    contract = get_contract("test_purity_tool")
    assert contract.classification == "read"
    assert contract.idempotent is True


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
    contract = get_contract(tool_name)
    assert contract.classification == expected
