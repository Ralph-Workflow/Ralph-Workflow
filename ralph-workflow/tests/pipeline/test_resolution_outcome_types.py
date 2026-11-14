from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from ralph.config.loader import ConfigLoadError, ConfigTomlError, load_toml
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
    WaitingOutcome,
    assert_never,
)


def test_stop_resolution_outcome_is_closed_over_the_six_contract_variants() -> None:
    assert set(get_args(StopResolutionOutcome.__value__)) == {
        ResolvedOutcome,
        WaitingOutcome,
        RetryLaterOutcome,
        NextStrategyOutcome,
        ConfigFailureOutcome,
        UnknownOutcome,
    }


def test_stop_resolution_outcome_payloads_are_frozen() -> None:
    parse_failure = ConfigFailureOutcome(Path("broken.toml"), "invalid value")
    unknown = UnknownOutcome(RuntimeError("unclassified"))

    assert parse_failure.file == Path("broken.toml")
    assert parse_failure.parse_error == "invalid value"
    assert str(unknown.error) == "unclassified"
    assert hash(parse_failure)


def test_retry_later_requires_exactly_one_resume_condition() -> None:
    assert RetryLaterOutcome(until=120).until == 120
    assert RetryLaterOutcome(on_change="configuration changes").on_change == "configuration changes"
    with pytest.raises(ValueError, match="exactly one"):
        RetryLaterOutcome()
    with pytest.raises(ValueError, match="exactly one"):
        RetryLaterOutcome(until=120, on_change="configuration changes")


def test_config_loader_raises_typed_error_with_file_and_parse_detail(tmp_path: Path) -> None:
    config_file = tmp_path / "ralph-workflow.toml"
    config_file.write_text("broken = [", encoding="utf-8")

    with pytest.raises(ConfigLoadError) as raised:
        load_toml(config_file)

    assert isinstance(raised.value, ConfigTomlError)
    assert raised.value.file == config_file
    assert raised.value.parse_error
    outcome = ConfigFailureOutcome.from_error(raised.value)
    assert outcome.file == config_file
    assert outcome.parse_error == raised.value.parse_error


def exhaustive_outcome_name(outcome: StopResolutionOutcome) -> str:
    match outcome:
        case ResolvedOutcome():
            return "resolved"
        case WaitingOutcome():
            return "waiting"
        case RetryLaterOutcome():
            return "retry_later"
        case NextStrategyOutcome():
            return "next_strategy"
        case ConfigFailureOutcome():
            return "config_failure"
        case UnknownOutcome():
            return "unknown"
    assert_never(outcome)


def test_assert_never_is_available_for_exhaustive_consumers() -> None:
    assert exhaustive_outcome_name(ResolvedOutcome()) == "resolved"
