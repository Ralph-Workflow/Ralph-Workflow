"""No two differently-configured agents may share one capture file.

Seven rounds fixed this family by family -- headless Claude, ccs
aliases, dynamic families, effort suffixes, the alias table form, the
alias string form, then "the whole invocation", which digested four
fields. The shipped config defeats a four-field key immediately:
``ralph-workflow-agents.toml`` puts ``--dangerously-skip-permissions``
in ``yolo_flag``, so two ``[agents.X]`` entries differing only in their
permission mode wrote one file. ``output_flag``, ``print_flag``,
``streaming_flag``, ``session_flag`` and ``verbose_flag`` all reach argv
the same way and were all outside the key.

Two agents sharing a capture is not a cosmetic filename problem: each
grades the other's bytes for corruption and quotes the other's
transport failures back to the operator.

This module does not enumerate fields. It reads them off ``AgentConfig``
and requires every one to separate, so a field added later is covered
the day it is added rather than in the round after it causes an
incident.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

_WORKSPACE = Path("/w")

#: Values to try for a field whose type this module has not been taught.
#: A field that none of these fit fails the test by name rather than
#: silently going unchecked.
_CANDIDATES: tuple[object, ...] = (
    "a-distinguishing-value",
    True,
    False,
    *tuple(AgentTransport),
    *tuple(JsonParserType),
)


def _capture_name(config: AgentConfig) -> str:
    return raw_log_path_for(
        _WORKSPACE, raw_log_unit_id_for(config), model=config.model
    ).name


def _variant_differing_in(field: str, base: AgentConfig) -> AgentConfig | None:
    """Build a config differing from ``base`` in ``field`` alone."""
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
    """Any difference in configuration means a different agent."""
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
    """The exact pairing the previous round claimed to have closed.

    The bundled agent config carries the permission mode in
    ``yolo_flag``. The round that named this pairing keyed on ``cmd``,
    where it is not.
    """
    plan = AgentConfig(cmd="claude", yolo_flag="--permission-mode plan")
    yolo = AgentConfig(cmd="claude", yolo_flag="--dangerously-skip-permissions")

    assert _capture_name(plan) != _capture_name(yolo)


def test_a_valueless_model_flag_still_separates() -> None:
    """The readable half of the name cannot carry this difference.

    The filename's model suffix comes from the VALUE tokens of
    ``model_flag``, so a flag with none -- ``--thinking`` against
    ``--no-thinking`` -- leaves both agents with an identical readable
    prefix. Only the digest can tell them apart, which is why the flag
    has to be inside it and not merely rendered from it.
    """
    thinking = AgentConfig(cmd="pi", model_flag="--thinking")
    not_thinking = AgentConfig(cmd="pi", model_flag="--no-thinking")

    assert _capture_name(thinking) != _capture_name(not_thinking)


def test_two_agents_that_are_the_same_invocation_still_share_one() -> None:
    """Separation is by DIFFERENCE, not by object identity.

    A key that separated equal configs would file each phase of one run
    under a new name and leave the operator hunting for the transcript.
    """
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
    """Regression pins for the pairings earlier rounds each closed once."""
    assert _capture_name(left) != _capture_name(right)
