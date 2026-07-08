"""Black-box test for the cross-repo handoff marker in pro-support.md.

The 2026-07 docs consolidation (wt-026) removed the "Forward-looking
engine capabilities pending contract amendment" H2 because its
"pending contract amendment" framing leaked internal contract
status into reader-facing prose. The forward-looking content was
already documented under "Late-marker adoption", "Custom pipeline
DI", and "State observability"; removing the H2 only stripped the
prose cruft, not the engine-symbol documentation. This test now
serves as a regression guard against re-introduction of that cruft
header.
"""

from __future__ import annotations

from pathlib import Path

PRO_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "docs" / "sphinx" / "pro-support.md"
FORWARD_LOOKING_HEADER = "Forward-looking engine capabilities pending contract amendment"
REQUIRED_SYMBOLS = ("ProMarkerWatcher", "ProPipelineHooks", "PipelineStateSnapshot")


def test_pro_contract_md_omits_removed_forward_looking_cruft_header() -> None:
    """The removed 'Forward-looking ... pending contract amendment' H2 must not return.

    The 2026-07 docs consolidation removed this H2 because the
    'pending contract amendment' framing leaks internal contract
    status into reader-facing prose. The engine symbols
    (ProMarkerWatcher, ProPipelineHooks, PipelineStateSnapshot) are
    still documented in the surviving sections; this test only
    guards against re-introducing the cruft header.
    """
    assert PRO_CONTRACT_PATH.exists(), f"missing {PRO_CONTRACT_PATH}"
    text = PRO_CONTRACT_PATH.read_text(encoding="utf-8")

    assert FORWARD_LOOKING_HEADER not in text, (
        f"pro-support.md must not contain the removed cruft header: {FORWARD_LOOKING_HEADER!r}"
    )


def test_pro_contract_md_documents_required_engine_symbols() -> None:
    """The engine symbols documented by the removed H2 must still appear elsewhere.

    The removed H2 was redundant with the surviving sections
    (Late-marker adoption, Custom pipeline DI, State observability)
    which document the same symbols. This test guards against any
    future refactor that drops the public symbol documentation.
    """
    assert PRO_CONTRACT_PATH.exists(), f"missing {PRO_CONTRACT_PATH}"
    text = PRO_CONTRACT_PATH.read_text(encoding="utf-8")

    missing = [name for name in REQUIRED_SYMBOLS if name not in text]
    assert not missing, f"pro-support.md is missing required engine symbols: {missing}"
