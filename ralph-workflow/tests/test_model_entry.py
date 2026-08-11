"""Direct unit tests for the ModelEntry dataclass.

Covers the new ``modalities_input`` retention and ``provider_slug``
normalization behavior added for criterion 14. Backward compatibility
is also exercised: the legacy ``(id, name, provider)`` construction
form must keep working, and ``model_validate`` must continue to
accept entries that omit ``modalities`` entirely.
"""

from __future__ import annotations

import pytest

from ralph.api.model_entry import ModelEntry


def test_model_entry_legacy_three_field_construction_still_works() -> None:
    """Backward compatibility: legacy 3-field construction defaults the new fields."""
    entry = ModelEntry(id="anthropic/claude-sonnet-4", name="Claude Sonnet 4", provider="anthropic")

    assert entry.id == "anthropic/claude-sonnet-4"
    assert entry.name == "Claude Sonnet 4"
    assert entry.provider == "anthropic"
    assert entry.modalities_input == ()
    assert entry.provider_slug is None


def test_model_entry_legacy_construction_via_model_validate() -> None:
    """Legacy 3-field construction via ``model_validate`` defaults the new fields."""
    entry = ModelEntry.model_validate(
        {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4", "provider": "anthropic"}
    )

    assert entry.id == "anthropic/claude-sonnet-4"
    assert entry.name == "Claude Sonnet 4"
    assert entry.provider == "anthropic"
    assert entry.modalities_input == ()
    assert entry.provider_slug == "anthropic"


def test_model_entry_modalities_input_retained_from_catalog() -> None:
    """``modalities.input`` is preserved as a tuple of strings on the entry."""
    entry = ModelEntry.model_validate(
        {
            "id": "anthropic/claude-sonnet-4",
            "name": "Claude Sonnet 4",
            "provider": "anthropic",
            "modalities": {
                "input": ["text", "image", "pdf"],
                "output": ["text"],
            },
        }
    )

    assert entry.modalities_input == ("text", "image", "pdf")


def test_model_entry_modalities_input_defaults_to_empty_tuple_when_omitted() -> None:
    """An entry without a ``modalities`` field carries an empty input tuple."""
    entry = ModelEntry.model_validate(
        {"id": "anthropic/claude-sonnet-4", "provider": "anthropic"}
    )

    assert entry.modalities_input == ()


def test_model_entry_modalities_input_defaults_to_empty_tuple_when_input_omitted() -> None:
    """A ``modalities`` block without ``input`` still yields an empty tuple."""
    entry = ModelEntry.model_validate(
        {"id": "anthropic/claude-sonnet-4", "provider": "anthropic", "modalities": {}}
    )

    assert entry.modalities_input == ()


def test_model_entry_modalities_input_must_be_list_of_strings() -> None:
    """A non-string ``modalities.input`` element raises ``ValueError``."""
    with pytest.raises(ValueError, match="modalities.input"):
        ModelEntry.model_validate(
            {
                "id": "anthropic/claude-sonnet-4",
                "provider": "anthropic",
                "modalities": {"input": ["text", 42]},
            }
        )


def test_model_entry_modalities_block_must_be_dict() -> None:
    """A ``modalities`` value that is not a dict raises ``ValueError``."""
    with pytest.raises(ValueError, match="modalities"):
        ModelEntry.model_validate(
            {
                "id": "anthropic/claude-sonnet-4",
                "provider": "anthropic",
                "modalities": "image,text",
            }
        )


def test_model_entry_modalities_input_must_be_list() -> None:
    """``modalities.input`` must be a list when present."""
    with pytest.raises(ValueError, match="modalities.input"):
        ModelEntry.model_validate(
            {
                "id": "anthropic/claude-sonnet-4",
                "provider": "anthropic",
                "modalities": {"input": "text"},
            }
        )


def test_model_entry_provider_slug_normalized_to_lowercase() -> None:
    """Uppercase provider slug normalises to lowercase via ``provider_slug``."""
    entry = ModelEntry.model_validate(
        {"id": "Anthropic/Claude-Sonnet-4", "name": "Claude Sonnet 4", "provider": "Anthropic"}
    )

    assert entry.provider == "Anthropic"
    assert entry.provider_slug == "anthropic"


def test_model_entry_provider_slug_collapses_disallowed_chars() -> None:
    """Disallowed characters in the provider slug collapse to ``-``."""
    entry = ModelEntry.model_validate(
        {
            "id": "Bedrock US/Claude-Sonnet-4",
            "provider": "Bedrock US",
        }
    )

    assert entry.provider == "Bedrock US"
    assert entry.provider_slug == "bedrock-us"


def test_model_entry_provider_slug_none_when_provider_missing() -> None:
    """``provider_slug`` is ``None`` when ``provider`` is missing."""
    entry = ModelEntry.model_validate({"id": "local/custom"})

    assert entry.provider is None
    assert entry.provider_slug is None


def test_model_entry_provider_slug_preserves_underscores_and_dashes() -> None:
    """Underscores and dashes are retained in the normalized slug."""
    entry = ModelEntry.model_validate(
        {"id": "amazon-bedrock/claude-sonnet-4", "provider": "amazon-bedrock"}
    )

    assert entry.provider_slug == "amazon-bedrock"


def test_model_entry_with_modalities_and_normalized_provider() -> None:
    """Combined: modalities_input preserved AND provider_slug normalized."""
    entry = ModelEntry.model_validate(
        {
            "id": "Anthropic/Claude-Sonnet-4",
            "name": "Claude Sonnet 4",
            "provider": "Anthropic",
            "modalities": {"input": ["text", "image"], "output": ["text"]},
        }
    )

    assert entry.modalities_input == ("text", "image")
    assert entry.provider == "Anthropic"
    assert entry.provider_slug == "anthropic"


def test_model_entry_is_hashable_with_modalities_input() -> None:
    """The dataclass stays hashable so callers can dedupe entries across catalog refreshes."""
    entry_a = ModelEntry.model_validate(
        {
            "id": "anthropic/claude-sonnet-4",
            "provider": "anthropic",
            "modalities": {"input": ["text", "image"]},
        }
    )
    entry_b = ModelEntry.model_validate(
        {
            "id": "anthropic/claude-sonnet-4",
            "provider": "anthropic",
            "modalities": {"input": ["text", "image"]},
        }
    )

    assert hash(entry_a) == hash(entry_b)
    assert entry_a == entry_b
