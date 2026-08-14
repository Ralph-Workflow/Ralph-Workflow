from __future__ import annotations

from ralph.config.models import GeneralConfig


def _GeneralConfigStub() -> GeneralConfig:
    """Return the real general config with its declared defaults.

    This used to be a hand-written dataclass mirroring every ``GeneralConfig``
    field. Mirrors drift: production added ``agent_no_output_at_start_seconds``
    and the mirror did not, so the invocation path raised ``AttributeError``
    and this suite went red without anyone touching it. Building the real model
    with its own defaults cannot drift, and keeps the test honest about the
    configuration production actually reads.
    """
    return GeneralConfig(verbosity=0, max_same_agent_retries=0)
