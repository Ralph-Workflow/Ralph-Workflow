"""Tests that FanOutEffect is phase-agnostic.

Verifies that the fan-out mechanism works for any phase whose parallelization
policy is declared, not just phases named 'development'.
"""

from __future__ import annotations

import ast
import pathlib
import typing

from ralph.pipeline import effects
from ralph.pipeline.effects import FanOutEffect

EFFECTS_PATH = (
    pathlib.Path(__file__).parent.parent / "ralph" / "pipeline" / "effects" / "__init__.py"
)


class TestFanOutEffectIsPhaseAgnostic:
    """FanOutEffect carries an explicit phase field rather than assuming 'development'."""

    def test_fan_out_effect_accepts_arbitrary_phase_name(self) -> None:
        effect = FanOutEffect(work_units=(), max_workers=2, phase="partition_work")
        assert effect.phase == "partition_work"

    def test_fan_out_effect_accepts_non_development_phase(self) -> None:
        effect = FanOutEffect(work_units=(), max_workers=1, phase="custom_parallel_phase")
        assert effect.phase == "custom_parallel_phase"

    def test_fan_out_effect_phase_defaults_to_empty_string(self) -> None:
        effect = FanOutEffect(work_units=(), max_workers=1)
        assert effect.phase == ""

    def test_fan_out_effect_does_not_reference_development_in_name(self) -> None:
        assert "development" not in FanOutEffect.__name__.lower()

    def test_effects_module_has_no_fanout_development_class(self) -> None:
        source = EFFECTS_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        class_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        assert "FanOutDevelopmentEffect" not in class_names, (
            "FanOutDevelopmentEffect must not be a class definition in effects.py; "
            "it must only exist as a backward-compat alias via __getattr__."
        )

    def test_fan_out_effect_is_in_effect_union(self) -> None:


        args = typing.get_args(effects.Effect)
        assert FanOutEffect in args, "FanOutEffect must be in the Effect union type"

    def test_fanout_development_effect_alias_returns_fanout_effect(self) -> None:

        alias = effects.FanOutDevelopmentEffect
        assert alias is FanOutEffect, (
            "FanOutDevelopmentEffect must be a backward-compat alias for FanOutEffect"
        )
