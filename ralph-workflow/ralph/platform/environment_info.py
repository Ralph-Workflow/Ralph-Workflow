from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnvironmentInfo:
    ci: bool = False
    container: bool = False
    wsl: bool = False
    codespaces: bool = False
    ssh: bool = False

    def markers(self) -> list[str]:
        markers: list[str] = []
        if self.ci:
            markers.append("ci")
        if self.container:
            markers.append("container")
        if self.wsl:
            markers.append("wsl")
        if self.codespaces:
            markers.append("codespaces")
        if self.ssh:
            markers.append("ssh")
        return markers
