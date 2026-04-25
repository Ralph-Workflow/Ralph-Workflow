"""Tests for parallel pipeline effect types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields
from typing import get_type_hints

import pytest

from ralph.pipeline import effects
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState


def test_fan_out_effect_frozen() -> None:
    effect = effects.FanOutDevelopmentEffect(work_units=(), max_workers=2)

    with pytest.raises(FrozenInstanceError):
        effect.max_workers = 3  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library


def test_merge_effect_frozen() -> None:
    effect = effects.MergeIntegrationEffect(worker_states={}, base_branch="main")

    with pytest.raises(FrozenInstanceError):
        effect.base_branch = "develop"  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library


def test_fan_out_effect_fields() -> None:
    effect_fields = fields(effects.FanOutDevelopmentEffect)
    hints = get_type_hints(effects.FanOutDevelopmentEffect)

    assert [field.name for field in effect_fields] == ["work_units", "max_workers"]
    assert hints["work_units"] == tuple[WorkUnit, ...]
    assert hints["max_workers"] is int


def test_merge_effect_fields() -> None:
    effect_fields = fields(effects.MergeIntegrationEffect)
    hints = get_type_hints(effects.MergeIntegrationEffect)

    assert [field.name for field in effect_fields] == ["worker_states", "base_branch"]
    assert hints["worker_states"] == Mapping[str, WorkerState]
    assert hints["base_branch"] is str
