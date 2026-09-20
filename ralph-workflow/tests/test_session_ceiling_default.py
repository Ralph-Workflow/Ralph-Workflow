"""The generic session watchdog must not preempt the development timebox."""

from __future__ import annotations

from ralph.config.general_config import GeneralConfig
from ralph.timeout_defaults import MAX_SESSION_SECONDS


def test_default_session_ceiling_exceeds_default_development_deadline() -> None:
    config = GeneralConfig()

    assert config.agent_max_session_seconds == MAX_SESSION_SECONDS == 5700.0
    assert config.agent_max_session_seconds > 5400.0
