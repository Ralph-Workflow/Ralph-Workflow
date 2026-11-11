
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.runtime.environment import SysModuleProtocol


@dataclass(frozen=True)
class PythonVersionInfo:

    major: int
    minor: int
    micro: int
    releaselevel: str
    serial: int
    implementation: str
    executable: Path
    version: str

    @classmethod
    def from_sys(cls, sys_module: SysModuleProtocol) -> PythonVersionInfo:
        return cls(
            major=sys_module.version_info.major,
            minor=sys_module.version_info.minor,
            micro=sys_module.version_info.micro,
            releaselevel=sys_module.version_info.releaselevel,
            serial=sys_module.version_info.serial,
            implementation=sys_module.implementation.name,
            executable=Path(sys_module.executable),
            version=sys_module.version,
        )


__all__ = ["PythonVersionInfo"]
