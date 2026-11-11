from __future__ import annotations

import pytest

from ralph.mcp._model_flag_parser import parse_model_flag


@pytest.mark.parametrize(
    "flag,expected",
    [
        (
            "--model anthropic/claude-sonnet-4-6",
            ("anthropic", "claude-sonnet-4-6"),
        ),
        (
            "--provider ollama --model qwen2.5-coder",
            ("ollama", "qwen2.5-coder"),
        ),
        (
            "--provider=ollama --model=qwen2.5-coder",
            ("ollama", "qwen2.5-coder"),
        ),
        (
            "--provider ollama --model=qwen2.5-coder",
            ("ollama", "qwen2.5-coder"),
        ),
        (
            "--provider=ollama --model qwen2.5-coder",
            ("ollama", "qwen2.5-coder"),
        ),
        (
            "anthropic/claude-sonnet-4-6",
            ("anthropic", "claude-sonnet-4-6"),
        ),
        (
            "claude-opus-4-7",
            (None, "claude-opus-4-7"),
        ),
        (
            "--model gpt-5.6-flash",
            (None, "gpt-5.6-flash"),
        ),
        (
            "-m gpt-5.6-flash",
            (None, "gpt-5.6-flash"),
        ),
        (
            "-m anthropic/claude-sonnet-4-6",
            ("anthropic", "claude-sonnet-4-6"),
        ),
    ],
)
def test_parse_model_flag_supported_forms(
    flag: str, expected: tuple[str | None, str | None]
) -> None:
    assert parse_model_flag(flag) == expected


def test_parse_model_flag_empty_input_returns_none_pair() -> None:
    assert parse_model_flag("") == (None, None)


def test_parse_model_flag_whitespace_only_input_returns_none_pair() -> None:
    assert parse_model_flag("   \t  ") == (None, None)


def test_parse_model_flag_none_like_input_returns_none_pair() -> None:
    assert parse_model_flag("") == (None, None)


def test_parse_model_flag_only_provider_flag_without_model_returns_none_pair() -> None:
    assert parse_model_flag("--provider ollama") == (None, None)


def test_parse_model_flag_unbalanced_quotes_degrade_gracefully() -> None:
    assert parse_model_flag('"--model anthropic/claude') == (None, None)


def test_parse_model_flag_normalises_provider_slug_to_lowercase() -> None:
    provider, model_id = parse_model_flag("--model Anthropic/Claude-Sonnet-4-6")
    assert provider == "anthropic"
    assert model_id == "Claude-Sonnet-4-6"


def test_parse_model_flag_collapses_disallowed_chars_in_provider_slug() -> None:
    provider, model_id = parse_model_flag("--provider 'Bedrock US' --model claude-sonnet-4-6")
    assert provider == "bedrock-us"
    assert model_id == "claude-sonnet-4-6"


def test_parse_model_flag_preserves_model_id_verbatim() -> None:
    _, model_id = parse_model_flag("--model claude-sonnet-4-6")
    assert model_id == "claude-sonnet-4-6"


def test_parse_model_flag_qualified_form_overrides_separate_provider_flag() -> None:
    provider, model_id = parse_model_flag("--provider openai --model anthropic/claude-sonnet-4-6")
    assert provider == "anthropic"
    assert model_id == "claude-sonnet-4-6"


def test_parse_model_flag_trailing_slash_with_no_model_returns_none_pair() -> None:
    assert parse_model_flag("--model anthropic/") == (None, None)


def test_parse_model_flag_unknown_extra_flag_is_ignored() -> None:
    provider, model_id = parse_model_flag(
        "--reasoning-effort high --model anthropic/claude-sonnet-4-6"
    )
    assert provider == "anthropic"
    assert model_id == "claude-sonnet-4-6"


def test_parse_model_flag_returns_tuple_type() -> None:
    result = parse_model_flag("--model anthropic/claude-sonnet-4-6")
    assert isinstance(result, tuple)
    assert len(result) == 2
    provider, model_id = result
    assert provider == "anthropic"
    assert model_id == "claude-sonnet-4-6"


def test_parse_model_flag_second_non_flag_token_is_ignored() -> None:
    provider, model_id = parse_model_flag("qwen2.5-coder --some-other-flag value")
    assert provider is None
    assert model_id == "qwen2.5-coder"
