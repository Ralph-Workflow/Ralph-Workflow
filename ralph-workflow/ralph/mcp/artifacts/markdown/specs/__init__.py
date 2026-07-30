"""Registered markdown artifact specifications."""

from ralph.mcp.artifacts.markdown.specs.analysis_decision import ANALYSIS_DECISION_SPECS
from ralph.mcp.artifacts.markdown.specs.commit_cleanup import COMMIT_CLEANUP_SPEC
from ralph.mcp.artifacts.markdown.specs.commit_message import COMMIT_MESSAGE_SPEC
from ralph.mcp.artifacts.markdown.specs.development_result import DEVELOPMENT_RESULT_SPEC
from ralph.mcp.artifacts.markdown.specs.fix_result import FIX_RESULT_SPEC
from ralph.mcp.artifacts.markdown.specs.issues import ISSUES_SPEC
from ralph.mcp.artifacts.markdown.specs.plan import PLAN_SPEC
from ralph.mcp.artifacts.markdown.specs.product_spec import PRODUCT_SPEC
from ralph.mcp.artifacts.markdown.specs.smoke_test_result import SMOKE_TEST_RESULT_SPEC

__all__ = [
    "ANALYSIS_DECISION_SPECS",
    "COMMIT_CLEANUP_SPEC",
    "COMMIT_MESSAGE_SPEC",
    "DEVELOPMENT_RESULT_SPEC",
    "FIX_RESULT_SPEC",
    "ISSUES_SPEC",
    "PLAN_SPEC",
    "PRODUCT_SPEC",
    "SMOKE_TEST_RESULT_SPEC",
]
