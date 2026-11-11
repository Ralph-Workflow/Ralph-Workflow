from __future__ import annotations

import pytest

import ralph.agents.parsers.base as base_module
from ralph.agents.parsers.base import extract_error_message


class TestExtractErrorMessage:
    @pytest.mark.parametrize(
        "parser_factory,case",
        [
            ("ClaudeParser", {"error": {"message": "boom"}}),
            ("CodexParser", {"error": {"message": "boom"}}),
            ("OpenCodeParser", {"error": {"message": "boom"}}),
            ("GeminiParser", {"error": {"message": "boom"}}),
            ("GenericParser", {"error": {"message": "boom"}}),
        ],
    )
    def test_error_message_from_error_dict(self, parser_factory: str, case: dict) -> None:
        assert extract_error_message(case) == "boom"

    @pytest.mark.parametrize(
        "parser_factory,case",
        [
            ("ClaudeParser", {"error": {"type": "teapot"}}),
            ("CodexParser", {"error": {"type": "teapot"}}),
            ("OpenCodeParser", {"error": {"type": "teapot"}}),
            ("GeminiParser", {"error": {"type": "teapot"}}),
            ("GenericParser", {"error": {"type": "teapot"}}),
        ],
    )
    def test_error_type_fallback(self, parser_factory: str, case: dict) -> None:
        assert extract_error_message(case) == "teapot"

    @pytest.mark.parametrize(
        "parser_factory,case",
        [
            ("ClaudeParser", {"error": {"name": "X"}}),
            ("CodexParser", {"error": {"name": "X"}}),
            ("OpenCodeParser", {"error": {"name": "X"}}),
            ("GeminiParser", {"error": {"name": "X"}}),
            ("GenericParser", {"error": {"name": "X"}}),
        ],
    )
    def test_error_name_fallback(self, parser_factory: str, case: dict) -> None:
        assert extract_error_message(case) == "X"

    @pytest.mark.parametrize(
        "parser_factory,case",
        [
            ("ClaudeParser", {"error": "raw-fail"}),
            ("CodexParser", {"error": "raw-fail"}),
            ("OpenCodeParser", {"error": "raw-fail"}),
            ("GeminiParser", {"error": "raw-fail"}),
            ("GenericParser", {"error": "raw-fail"}),
        ],
    )
    def test_error_raw_string(self, parser_factory: str, case: dict) -> None:
        assert extract_error_message(case) == "raw-fail"

    @pytest.mark.parametrize(
        "parser_factory,case",
        [
            ("ClaudeParser", {"message": "alt"}),
            ("CodexParser", {"message": "alt"}),
            ("OpenCodeParser", {"message": "alt"}),
            ("GeminiParser", {"message": "alt"}),
            ("GenericParser", {"message": "alt"}),
        ],
    )
    def test_message_fallback(self, parser_factory: str, case: dict) -> None:
        assert extract_error_message(case) == "alt"

    @pytest.mark.parametrize(
        "parser_factory",
        ["ClaudeParser", "CodexParser", "OpenCodeParser", "GeminiParser", "GenericParser"],
    )
    def test_empty_object_returns_unknown_error(self, parser_factory: str) -> None:
        assert extract_error_message({}) == "unknown error"

    @pytest.mark.parametrize(
        "parser_factory",
        ["ClaudeParser", "CodexParser", "OpenCodeParser", "GeminiParser", "GenericParser"],
    )
    def test_msg_field_fallback(self, parser_factory: str) -> None:
        assert extract_error_message({"msg": "msg-fallback"}) == "msg-fallback"

    def test_opencode_api_error_reports_nested_data_message(self) -> None:
        case = {
            "type": "error",
            "error": {
                "name": "APIError",
                "data": {
                    "message": ("This request requires more credits, or fewer max_tokens."),
                    "statusCode": 402,
                },
            },
        }

        assert extract_error_message(case) == (
            "This request requires more credits, or fewer max_tokens."
        )

    def test_error_name_still_used_when_no_nested_message(self) -> None:
        assert extract_error_message({"error": {"name": "APIError"}}) == "APIError"

    def test_helper_lives_at_ralph_agents_parsers_base(self) -> None:
        assert hasattr(base_module, "extract_error_message")
        assert callable(base_module.extract_error_message)
