from __future__ import annotations

from tests._scenario_operator_line import OperatorLine


class ScenarioDisplay:
    def __init__(self) -> None:
        self.lines: list[OperatorLine] = []

    def emit_warn_line(self, _unit_id: str, _tag: str, message: str) -> None:
        self.lines.append(OperatorLine("WARNING", message))
