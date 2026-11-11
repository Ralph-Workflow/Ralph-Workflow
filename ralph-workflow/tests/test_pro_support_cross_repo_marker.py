from __future__ import annotations

from pathlib import Path

PRO_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "docs" / "sphinx" / "pro-support.md"
FORWARD_LOOKING_HEADER = "Forward-looking engine capabilities pending contract amendment"
REQUIRED_SYMBOLS = ("ProMarkerWatcher", "ProPipelineHooks", "PipelineStateSnapshot")


def test_pro_contract_md_omits_removed_forward_looking_cruft_header() -> None:
    assert PRO_CONTRACT_PATH.exists(), f"missing {PRO_CONTRACT_PATH}"
    text = PRO_CONTRACT_PATH.read_text(encoding="utf-8")

    assert FORWARD_LOOKING_HEADER not in text, (
        f"pro-support.md must not contain the removed cruft header: {FORWARD_LOOKING_HEADER!r}"
    )


def test_pro_contract_md_documents_required_engine_symbols() -> None:
    assert PRO_CONTRACT_PATH.exists(), f"missing {PRO_CONTRACT_PATH}"
    text = PRO_CONTRACT_PATH.read_text(encoding="utf-8")

    missing = [name for name in REQUIRED_SYMBOLS if name not in text]
    assert not missing, f"pro-support.md is missing required engine symbols: {missing}"
