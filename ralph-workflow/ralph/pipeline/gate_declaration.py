from dataclasses import dataclass


@dataclass(frozen=True)
class GateDeclaration:
    value: str
    source: str
    command: str | None
    problem: str = ""

    @property
    def gate_key(self) -> str:
        return self.command if self.command is not None else self.value
