from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

_WORKSPACE = Path("/w")

_CANDIDATES: tuple[object, ...] = (
    "a-distinguishing-value",
    True,
    False,
    *tuple(AgentTransport),
    *tuple(JsonParserType),
)


def _capture_name(config: AgentConfig) -> str:
    return raw_log_path_for(_WORKSPACE, raw_log_unit_id_for(config), model=config.model).name


def _variant_differing_in(field: str, base: AgentConfig) -> AgentConfig | None:
    for candidate in _CANDIDATES:
        if candidate == getattr(base, field):
            continue
        try:
            variant = AgentConfig(**{**base.model_dump(), field: candidate})
        except ValidationError:
            continue
        if getattr(variant, field) != getattr(base, field):
            return variant
    return None


def test_every_agent_config_field_separates_the_capture() -> None:
    base = AgentConfig(cmd="anagent")
    unseparated: list[str] = []
    unexercised: list[str] = []
    for field in sorted(type(base).model_fields):
        variant = _variant_differing_in(field, base)
        if variant is None:
            unexercised.append(field)
            continue
        if _capture_name(variant) == _capture_name(base):
            unseparated.append(field)
    assert unseparated == []
    assert unexercised == [], (
        "no candidate value fits these fields, so they are not being checked; "
        "add one to _CANDIDATES"
    )


def test_the_shipped_permission_flags_do_not_share_a_capture() -> None:
    plan = AgentConfig(cmd="claude", yolo_flag="--permission-mode plan")
    yolo = AgentConfig(cmd="claude", yolo_flag="--dangerously-skip-permissions")

    assert _capture_name(plan) != _capture_name(yolo)


def test_a_valueless_model_flag_still_separates() -> None:
    thinking = AgentConfig(cmd="pi", model_flag="--thinking")
    not_thinking = AgentConfig(cmd="pi", model_flag="--no-thinking")

    assert _capture_name(thinking) != _capture_name(not_thinking)


def test_two_agents_that_are_the_same_invocation_still_share_one() -> None:
    first = AgentConfig(cmd="claude", model_flag="--model m", yolo_flag="--y")
    second = AgentConfig(cmd="claude", model_flag="--model m", yolo_flag="--y")

    assert _capture_name(first) == _capture_name(second)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            AgentConfig(cmd="ccs claude", model_flag="--model anthropic/opus"),
            AgentConfig(cmd="claude", output_flag="--output-format=stream-json"),
        ),
        (
            AgentConfig(cmd="ccs nano", transport=AgentTransport.GENERIC),
            AgentConfig(cmd="nanocoder", transport=AgentTransport.NANOCODER),
        ),
    ],
)
def test_the_previously_reported_collisions_stay_closed(
    left: AgentConfig, right: AgentConfig
) -> None:
    assert _capture_name(left) != _capture_name(right)
