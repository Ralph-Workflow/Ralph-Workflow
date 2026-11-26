from __future__ import annotations

from ralph.config.conflict_resolution_config import ConflictResolutionConfig
from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution.session import resolution_round_config


def test_a_resolver_that_only_reads_is_cut_after_thirty_minutes_not_ninety_five() -> None:
    config = UnifiedConfig()

    round_config = resolution_round_config(config, None)

    assert ConflictResolutionConfig().round_wall_clock_seconds == 1800.0
    assert round_config.general.agent_max_session_seconds == 1800.0


def test_a_tighter_session_ceiling_is_kept() -> None:
    config = UnifiedConfig()
    config = config.model_copy(
        update={"general": config.general.model_copy(update={"agent_max_session_seconds": 600.0})}
    )

    assert resolution_round_config(config, None).general.agent_max_session_seconds == 600.0


def test_the_round_ceiling_is_configurable() -> None:
    config = UnifiedConfig()
    config = config.model_copy(
        update={
            "conflict_resolution": config.conflict_resolution.model_copy(
                update={"round_wall_clock_seconds": 2400.0}
            )
        }
    )

    assert resolution_round_config(config, None).general.agent_max_session_seconds == 2400.0


def test_the_idle_timeout_still_follows_the_resolution_limit() -> None:
    round_config = resolution_round_config(UnifiedConfig(), 120.0)

    assert round_config.general.agent_idle_timeout_seconds == 120.0
