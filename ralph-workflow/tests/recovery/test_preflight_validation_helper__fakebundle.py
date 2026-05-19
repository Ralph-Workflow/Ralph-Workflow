from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.recovery.test_preflight_validation_helper__fakechainconfig import _FakeChainConfig


class _FakeBundle:
    def __init__(
        self,
        chains: dict[str, _FakeChainConfig],
        drains: dict[str, object],
        phases: dict[str, object],
    ) -> None:
        self.agents = type("_Agents", (), {"agent_chains": chains, "agent_drains": drains})()
        self.pipeline = type("_Pipeline", (), {"phases": phases})()
