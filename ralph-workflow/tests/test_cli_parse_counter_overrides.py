"""Unit tests for CLI."""

from __future__ import annotations

import pytest
import rich_click as click

from ralph.cli.main import parse_counter_overrides


class TestParseCounterOverrides:
    """Tests for _parse_counter_overrides helper."""

    def test_parses_single_valid_entry(self) -> None:
        result = parse_counter_overrides(["iteration=3"])
        assert result == {"iteration": 3}

    def test_parses_multiple_entries(self) -> None:
        result = parse_counter_overrides(["iteration=3", "reviewer_pass=1"])
        assert result == {"iteration": 3, "reviewer_pass": 1}

    def test_empty_list_returns_empty_dict(self) -> None:
        assert parse_counter_overrides([]) == {}

    def test_missing_equals_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError, match="invalid format"):
            parse_counter_overrides(["iteration3"])

    def test_blank_name_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError, match="blank counter name"):
            parse_counter_overrides(["=5"])

    def test_non_integer_value_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError, match="not a valid integer"):
            parse_counter_overrides(["iteration=abc"])

    def test_zero_value_is_valid(self) -> None:
        result = parse_counter_overrides(["reviewer_pass=0"])
        assert result == {"reviewer_pass": 0}
