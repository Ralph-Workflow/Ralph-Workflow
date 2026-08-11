"""Tests for the transport-neutral model-flag parser.

Covers every supported ``--model`` / ``--provider`` grammar accepted by
:func:`ralph.mcp._model_flag_parser.parse_model_flag`:

* ``--model anthropic/claude-sonnet-4-6`` — qualified provider/model.
* ``--provider ollama --model qwen2.5-coder`` — separate provider flag.
* ``--provider=ollama --model=qwen2.5-coder`` — ``=``-separated form.
* ``anthropic/claude-sonnet-4-6`` — bare qualified name.
* ``claude-opus-4-7`` — bare model name (provider=None).

The parser must never raise on malformed input; every malformed flag
degrades to ``(None, None)`` so callers can fall back to their default
identity resolution path.
"""

from __future__ import annotations

import pytest

from ralph.mcp._model_flag_parser import parse_model_flag


@pytest.mark.parametrize(
    "flag,expected",
    [
        # Qualified --model form
        (
            "--model anthropic/claude-sonnet-4-6",
            ("anthropic", "claude-sonnet-4-6"),
        ),
        # Separate --provider and --model flags (space-separated)
        (
            "--provider ollama --model qwen2.5-coder",
            ("ollama", "qwen2.5-coder"),
        ),
        # = -separated flag form
        (
            "--provider=ollama --model=qwen2.5-coder",
            ("ollama", "qwen2.5-coder"),
        ),
        # Mixed = / space-separated flag form
        (
            "--provider ollama --model=qwen2.5-coder",
            ("ollama", "qwen2.5-coder"),
        ),
        (
            "--provider=ollama --model qwen2.5-coder",
            ("ollama", "qwen2.5-coder"),
        ),
        # Bare qualified name (no flag prefix)
        (
            "anthropic/claude-sonnet-4-6",
            ("anthropic", "claude-sonnet-4-6"),
        ),
        # Bare model name (no provider, no flag prefix) -> provider=None
        (
            "claude-opus-4-7",
            (None, "claude-opus-4-7"),
        ),
        # Bare model name via --model flag with no provider hint
        (
            "--model gpt-5.6-flash",
            (None, "gpt-5.6-flash"),
        ),
        # Short -m flag form
        (
            "-m gpt-5.6-flash",
            (None, "gpt-5.6-flash"),
        ),
        # Qualified form via -m short flag
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
    """The parser accepts ``str`` only; an empty string degrades gracefully."""
    assert parse_model_flag("") == (None, None)


def test_parse_model_flag_only_provider_flag_without_model_returns_none_pair() -> None:
    assert parse_model_flag("--provider ollama") == (None, None)


def test_parse_model_flag_unbalanced_quotes_degrade_gracefully() -> None:
    """shlex raises ValueError on unbalanced quotes; the parser swallows it."""
    assert parse_model_flag('"--model anthropic/claude') == (None, None)


def test_parse_model_flag_normalises_provider_slug_to_lowercase() -> None:
    """Uppercase provider slug is lowercased before return."""
    provider, model_id = parse_model_flag("--model Anthropic/Claude-Sonnet-4-6")
    assert provider == "anthropic"
    assert model_id == "Claude-Sonnet-4-6"


def test_parse_model_flag_collapses_disallowed_chars_in_provider_slug() -> None:
    """Disallowed characters in the provider slug collapse to ``-``."""
    provider, model_id = parse_model_flag("--provider 'Bedrock US' --model claude-sonnet-4-6")
    assert provider == "bedrock-us"
    assert model_id == "claude-sonnet-4-6"


def test_parse_model_flag_preserves_model_id_verbatim() -> None:
    """Model identifiers are returned verbatim — only the provider slug is normalised."""
    _, model_id = parse_model_flag("--model claude-sonnet-4-6")
    assert model_id == "claude-sonnet-4-6"


def test_parse_model_flag_qualified_form_overrides_separate_provider_flag() -> None:
    """When both forms appear, the qualified ``provider/model`` form wins."""
    provider, model_id = parse_model_flag("--provider openai --model anthropic/claude-sonnet-4-6")
    assert provider == "anthropic"
    assert model_id == "claude-sonnet-4-6"


def test_parse_model_flag_trailing_slash_with_no_model_returns_none_pair() -> None:
    """``--model anthropic/`` (empty model_id) degrades to ``(None, None)``."""
    assert parse_model_flag("--model anthropic/") == (None, None)


def test_parse_model_flag_unknown_extra_flag_is_ignored() -> None:
    """Unknown flags (e.g. ``--reasoning-effort``) are ignored, not errors."""
    provider, model_id = parse_model_flag(
        "--reasoning-effort high --model anthropic/claude-sonnet-4-6"
    )
    assert provider == "anthropic"
    assert model_id == "claude-sonnet-4-6"


def test_parse_model_flag_returns_tuple_type() -> None:
    """Return type is always a 2-tuple of ``str | None`` values."""
    result = parse_model_flag("--model anthropic/claude-sonnet-4-6")
    assert isinstance(result, tuple)
    assert len(result) == 2
    provider, model_id = result
    assert provider == "anthropic"
    assert model_id == "claude-sonnet-4-6"


def test_parse_model_flag_second_non_flag_token_is_ignored() -> None:
    """The first non-flag token wins as the bare model name; later tokens are dropped."""
    provider, model_id = parse_model_flag("qwen2.5-coder --some-other-flag value")
    assert provider is None
    assert model_id == "qwen2.5-coder"
