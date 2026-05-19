"""Shared frozen base model for work unit models."""

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel


class FrozenWorkUnitModel(RalphBaseModel):
    """Shared base for frozen work unit models."""

    model_config = ConfigDict(frozen=True)


__all__ = ["FrozenWorkUnitModel"]
