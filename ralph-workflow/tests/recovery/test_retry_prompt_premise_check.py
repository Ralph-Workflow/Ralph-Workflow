from ralph.recovery import retry_prompt


def test_premise_check_anchor_is_not_exported() -> None:
    assert not hasattr(retry_prompt, "PREMISE_CHECK_BANNER")
    assert not hasattr(retry_prompt, "PREMISE_CHECK_INSTRUCTION")
    assert not hasattr(retry_prompt, "build_premise_check_block")


def test_retry_prompt_still_exposes_the_formatter_contract() -> None:
    assert hasattr(retry_prompt, "build_validation_retry_footer")
    assert hasattr(retry_prompt, "build_retry_error_block")
