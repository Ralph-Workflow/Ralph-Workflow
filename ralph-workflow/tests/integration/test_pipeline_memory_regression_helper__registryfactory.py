from __future__ import annotations

from typing import TYPE_CHECKING

from tests.integration.test_pipeline_memory_regression_helper__registryinstance import (
    _RegistryInstance,
)

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig


class _RegistryFactory:
    @classmethod
    def from_config(cls, config: UnifiedConfig) -> _RegistryInstance:
        del cls, config
        return _RegistryInstance()
