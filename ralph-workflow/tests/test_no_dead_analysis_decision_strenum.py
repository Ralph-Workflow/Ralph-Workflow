"""Contract test: AnalysisDecision StrEnum from ralph.config.enums must be deleted.

CLAUDE.md requires zero dead code. The AnalysisDecision StrEnum in
ralph.config.enums has no callers in the package and must be removed.
The BaseModel named AnalysisDecision in ralph.mcp.artifacts.typed_artifacts
is a different class and must remain.
"""

from __future__ import annotations

import importlib


def test_analysis_decision_strenum_is_removed_from_config_enums() -> None:
    """AnalysisDecision StrEnum must not exist in ralph.config.enums."""
    mod = importlib.import_module("ralph.config.enums")
    assert not hasattr(mod, "AnalysisDecision"), (
        "ralph.config.enums.AnalysisDecision still exists; it is dead code and must be removed."
    )


def test_analysis_decision_basemodel_in_typed_artifacts_still_present() -> None:
    """The AnalysisDecision BaseModel in typed_artifacts must not be removed."""
    mod = importlib.import_module("ralph.mcp.artifacts.typed_artifacts")
    assert hasattr(mod, "AnalysisDecision"), (
        "ralph.mcp.artifacts.typed_artifacts.AnalysisDecision was unexpectedly removed."
    )
