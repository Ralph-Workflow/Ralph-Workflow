"""Closed-enum planning profile preset for DesignSection."""

from __future__ import annotations

from typing import Literal

PlanningProfile = Literal["strict", "balanced", "minimal"]

__all__ = ["PlanningProfile"]
