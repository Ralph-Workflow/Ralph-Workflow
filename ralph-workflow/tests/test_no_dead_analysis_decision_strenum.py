from __future__ import annotations

import importlib


def test_analysis_decision_strenum_is_removed_from_config_enums() -> None:
    mod = importlib.import_module("ralph.config.enums")
    assert not hasattr(mod, "AnalysisDecision"), (
        "ralph.config.enums.AnalysisDecision still exists; it is dead code and must be removed."
    )


def test_analysis_decision_basemodel_in_typed_artifacts_still_present() -> None:
    mod = importlib.import_module("ralph.mcp.artifacts.typed_artifacts")
    assert hasattr(mod, "AnalysisDecision"), (
        "ralph.mcp.artifacts.typed_artifacts.AnalysisDecision was unexpectedly removed."
    )
