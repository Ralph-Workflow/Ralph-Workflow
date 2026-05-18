from __future__ import annotations

from tests.integration._ccs_config_stub import _CcsConfigStub
from tests.integration._general_config_stub import _GeneralConfigStub


class _ConfigStub:
    def __init__(self) -> None:
        self.general = _GeneralConfigStub()
        self.ccs = _CcsConfigStub()
        self.ccs_aliases: dict[str, str] = {}
