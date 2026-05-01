"""Tests for parallel pipeline effect types."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from typing import get_type_hints

import pytest

from ralph.pipeline import effects
from ralph.pipeline.work_units import WorkUnit


def test_fan_out_effect_frozen() -> None:
    effect = effects.FanOutEffect(work_units=(), max_workers=2)

    with pytest.raises(FrozenInstanceError):
        effect.max_workers = 3  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library


def test_fan_out_effect_fields() -> None:
    effect_fields = fields(effects.FanOutEffect)
    hints = get_type_hints(effects.FanOutEffect)

    assert [field.name for field in effect_fields] == [
        "work_units",
        "max_workers",
        "run_post_fanout_verification",
        "phase",
    ]
    assert hints["work_units"] == tuple[WorkUnit, ...]
    assert hints["max_workers"] is int
    assert hints["run_post_fanout_verification"] is bool
    assert hints["phase"] is str


def test_fan_out_effect_run_post_fanout_verification_defaults_false() -> None:
    effect = effects.FanOutEffect(work_units=(), max_workers=1)
    assert effect.run_post_fanout_verification is False


def test_fan_out_effect_run_post_fanout_verification_settable() -> None:
    effect = effects.FanOutEffect(
        work_units=(), max_workers=1, run_post_fanout_verification=True
    )
    assert effect.run_post_fanout_verification is True


def test_merge_integration_effect_not_in_effects() -> None:
    assert not hasattr(effects, "MergeIntegrationEffect")


def test_effect_union_does_not_include_merge_integration() -> None:
    import typing  # noqa: PLC0415
    args = typing.get_args(effects.Effect)
    names = [getattr(t, "__name__", str(t)) for t in args]
    assert "MergeIntegrationEffect" not in names
