from __future__ import annotations

from ralph.config.models import GeneralConfig


def _GeneralConfigStub() -> GeneralConfig:
    return GeneralConfig(verbosity=0, max_same_agent_retries=0)
