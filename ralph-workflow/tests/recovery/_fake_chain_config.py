from __future__ import annotations


class _FakeChainConfig:
    def __init__(self, agents: list[str], max_retries: int = 3) -> None:
        self.agents = agents
        self.max_retries = max_retries
