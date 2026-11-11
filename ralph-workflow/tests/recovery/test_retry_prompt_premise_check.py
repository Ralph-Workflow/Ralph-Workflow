"""The premise-check retry anchor is gone with the premise-check rule.

The kernel's Premise check rule was removed: the debugging skill teaches the
technique, and the deterministic retry formatter keeps only the failure-first
block, the pinned byte-exact text and the validation banner/footer.

# RALPH-TEST-REASON: the premise-check rule and its retry-prompt anchor are removed by this change; the deterministic retry formatter contract is covered by tests/recovery/test_retry_prompt_format.py.
"""

from ralph.recovery import retry_prompt


def test_premise_check_anchor_is_not_exported() -> None:
    """The removed premise-check helper must not come back."""
    assert not hasattr(retry_prompt, "PREMISE_CHECK_BANNER")
    assert not hasattr(retry_prompt, "PREMISE_CHECK_INSTRUCTION")
    assert not hasattr(retry_prompt, "build_premise_check_block")


def test_retry_prompt_still_exposes_the_formatter_contract() -> None:
    """The surviving formatter surface stays available."""
    assert hasattr(retry_prompt, "build_validation_retry_footer")
    assert hasattr(retry_prompt, "build_retry_error_block")
