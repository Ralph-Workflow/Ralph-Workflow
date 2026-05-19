from __future__ import annotations

from tests.integration.test_pipeline_memory_regression_helper__ccsconfigstub import (
    _CcsConfigStub,
)
from tests.integration.test_pipeline_memory_regression_helper__generalconfigstub import (
    _GeneralConfigStub,
)


class _ConfigStub:
    def __init__(self) -> None:
        self.general = _GeneralConfigStub()
        self.ccs = _CcsConfigStub()
        self.ccs_aliases: dict[str, str] = {}
