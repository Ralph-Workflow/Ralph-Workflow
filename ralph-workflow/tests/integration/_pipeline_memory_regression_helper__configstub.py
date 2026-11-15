from __future__ import annotations

from ralph.config.models import CcsConfig, UnifiedConfig
from tests.integration._pipeline_memory_regression_helper__generalconfigstub import (
    _GeneralConfigStub,
)


def _ConfigStub() -> UnifiedConfig:
    return UnifiedConfig(
        general=_GeneralConfigStub(),
        ccs=CcsConfig(),
        ccs_aliases={},
    )
