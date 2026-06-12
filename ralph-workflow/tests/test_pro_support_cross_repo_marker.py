"""Black-box test for the cross-repo handoff marker in :mod:`docs.agents.pro-contract`."""

from __future__ import annotations

from pathlib import Path

PRO_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "docs" / "agents" / "pro-contract.md"
FORWARD_LOOKING_HEADER = "Forward-looking engine capabilities pending contract amendment"
REQUIRED_SYMBOLS = ("ProMarkerWatcher", "ProPipelineHooks", "PipelineStateSnapshot")


def test_pro_contract_md_lists_forward_looking_engine_capabilities() -> None:
    assert PRO_CONTRACT_PATH.exists(), f"missing {PRO_CONTRACT_PATH}"
    text = PRO_CONTRACT_PATH.read_text(encoding="utf-8")

    section_start = text.find(FORWARD_LOOKING_HEADER)
    assert section_start >= 0, (
        f"pro-contract.md must contain a section header: {FORWARD_LOOKING_HEADER!r}"
    )
    section = text[section_start:]

    missing = [name for name in REQUIRED_SYMBOLS if name not in section]
    assert not missing, (
        f"pro-contract.md forward-looking section is missing symbols: {missing}"
    )
