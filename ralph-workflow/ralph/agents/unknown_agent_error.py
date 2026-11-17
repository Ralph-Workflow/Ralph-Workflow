from ralph.agents.registry import lookup_dynamic_alias_help


class UnknownAgentError(Exception):
    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        msg = f"Unknown agent: '{agent_name}'. Register the agent in the configuration."
        alias_help = lookup_dynamic_alias_help(agent_name)
        if alias_help is not None:
            msg = f"{msg} {alias_help}"
        super().__init__(msg)
