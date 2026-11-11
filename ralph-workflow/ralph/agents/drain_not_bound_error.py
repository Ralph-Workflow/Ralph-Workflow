

class DrainNotBoundError(Exception):

    def __init__(self, drain: str, available_drains: set[str]) -> None:
        self.drain = drain
        self.available_drains = available_drains
        available = sorted(available_drains)
        msg = (
            f"Drain '{drain}' is not bound to any agent chain in agents.toml. "
            f"Available drains: {available}. "
            f"Add a binding for '{drain}' in agent_drains or use a bound drain."
        )
        super().__init__(msg)
