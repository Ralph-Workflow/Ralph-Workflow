"""Testability sub-section for the plan design schema."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel

ForbiddenInTest = Literal[
    "time.sleep",
    "subprocess.run-no-timeout",
    "real-file-IO",
    "real-network",
    "global-mutation",
    "monkeypatch-of-prod",
    "unknown",
]

TestLayer = Literal[
    "unit",
    "integration",
    "subprocess_e2e",
    "property",
    "snapshot",
    "contract",
    "unknown",
]


class Testability(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    must_be_black_box: bool
    forbidden_in_tests: list[ForbiddenInTest] = Field(default_factory=list)
    required_test_layers: list[TestLayer] = Field(default_factory=list)
    clock_injection_required: bool | None = None
    max_unit_test_seconds: float | None = Field(default=None, gt=0, le=60)


__all__ = [
    "ForbiddenInTest",
    "TestLayer",
    "Testability",
]
