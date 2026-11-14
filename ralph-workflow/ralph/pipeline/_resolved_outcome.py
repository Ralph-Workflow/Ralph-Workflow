from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from ralph.git.resolution_failure_cause import ResolutionFailureCause

__all__ = ["ResolvedOutcome"]

_TUPLE_PAIR_LEN = 2


class _ResolvedOutcomeMeta(type):
    def __instancecheck__(cls, instance: object) -> bool:
        if super().__instancecheck__(instance):
            return True
        from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome

        return isinstance(instance, ResolutionOutcome) and instance.succeeded


@dataclass(frozen=True, slots=True)
class ResolvedOutcome(metaclass=_ResolvedOutcomeMeta):
    terminal_reason: object | None = None
    unresolved_paths: tuple[str, ...] = ()
    failure_cause: ResolutionFailureCause | None = None

    @property
    def succeeded(self) -> bool:
        return True

    @property
    def reason(self) -> object:
        return None

    def __iter__(self) -> Iterator[object]:
        return iter((True, ""))

    def __getitem__(self, index: int) -> object:
        return (True, "")[index]

    def __len__(self) -> int:
        return 2

    def __bool__(self) -> bool:
        return True

    def __hash__(self) -> int:
        return hash((self.succeeded, self.reason, self.unresolved_paths))


    def __eq__(self, other: object) -> bool:
        if (
            isinstance(other, tuple)
            and len(other) == _TUPLE_PAIR_LEN
            and isinstance(other[0], bool)
            and isinstance(other[1], str)
        ):
            return other == (True, "")
        return isinstance(other, ResolvedOutcome) and (
            self.succeeded,
            self.reason,
            self.unresolved_paths,
        ) == (other.succeeded, other.reason, other.unresolved_paths)

