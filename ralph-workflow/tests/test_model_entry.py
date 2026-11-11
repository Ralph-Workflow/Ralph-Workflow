from __future__ import annotations

import pytest

from ralph.api.model_entry import ModelEntry


def test_model_entry_legacy_three_field_construction_still_works() -> None:
    entry = ModelEntry(id="anthropic/claude-sonnet-4", name="Claude Sonnet 4", provider="anthropic")

    assert entry.id == "anthropic/claude-sonnet-4"
    assert entry.name == "Claude Sonnet 4"
    assert entry.provider == "anthropic"
    assert entry.modalities_input == ()
    assert entry.provider_slug is None


def test_model_entry_legacy_construction_via_model_validate() -> None:
    entry = ModelEntry.model_validate(
        {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4", "provider": "anthropic"}
    )

    assert entry.id == "anthropic/claude-sonnet-4"
    assert entry.name == "Claude Sonnet 4"
    assert entry.provider == "anthropic"
    assert entry.modalities_input == ()
    assert entry.provider_slug == "anthropic"


def test_model_entry_modalities_input_retained_from_catalog() -> None:
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
    entry = ModelEntry.model_validate({"id": "anthropic/claude-sonnet-4", "provider": "anthropic"})

    assert entry.modalities_input == ()


def test_model_entry_modalities_input_defaults_to_empty_tuple_when_input_omitted() -> None:
    entry = ModelEntry.model_validate(
        {"id": "anthropic/claude-sonnet-4", "provider": "anthropic", "modalities": {}}
    )

    assert entry.modalities_input == ()


def test_model_entry_modalities_input_must_be_list_of_strings() -> None:
    with pytest.raises(ValueError, match="modalities.input"):
        ModelEntry.model_validate(
            {
                "id": "anthropic/claude-sonnet-4",
                "provider": "anthropic",
                "modalities": {"input": ["text", 42]},
            }
        )


def test_model_entry_modalities_block_must_be_dict() -> None:
    with pytest.raises(ValueError, match="modalities"):
        ModelEntry.model_validate(
            {
                "id": "anthropic/claude-sonnet-4",
                "provider": "anthropic",
                "modalities": "image,text",
            }
        )


def test_model_entry_modalities_input_must_be_list() -> None:
    with pytest.raises(ValueError, match="modalities.input"):
        ModelEntry.model_validate(
            {
                "id": "anthropic/claude-sonnet-4",
                "provider": "anthropic",
                "modalities": {"input": "text"},
            }
        )


def test_model_entry_provider_slug_normalized_to_lowercase() -> None:
    entry = ModelEntry.model_validate(
        {"id": "Anthropic/Claude-Sonnet-4", "name": "Claude Sonnet 4", "provider": "Anthropic"}
    )

    assert entry.provider == "Anthropic"
    assert entry.provider_slug == "anthropic"


def test_model_entry_provider_slug_collapses_disallowed_chars() -> None:
    entry = ModelEntry.model_validate(
        {
            "id": "Bedrock US/Claude-Sonnet-4",
            "provider": "Bedrock US",
        }
    )

    assert entry.provider == "Bedrock US"
    assert entry.provider_slug == "bedrock-us"


def test_model_entry_provider_slug_none_when_provider_missing() -> None:
    entry = ModelEntry.model_validate({"id": "local/custom"})

    assert entry.provider is None
    assert entry.provider_slug is None


def test_model_entry_provider_slug_preserves_underscores_and_dashes() -> None:
    entry = ModelEntry.model_validate(
        {"id": "amazon-bedrock/claude-sonnet-4", "provider": "amazon-bedrock"}
    )

    assert entry.provider_slug == "amazon-bedrock"


def test_model_entry_with_modalities_and_normalized_provider() -> None:
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
