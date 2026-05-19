"""PolicyFlagSet — set of Ralph policy flags."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts._policy_flag import PolicyFlag

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


class PolicyFlagSet:
    """Set of Ralph policy flags."""

    def __init__(self, values: Iterable[PolicyFlag] | None = None) -> None:
        self._values = frozenset(values or ())

    def contains(self, flag: PolicyFlag) -> bool:
        return flag in self._values

    def insert(self, flag: PolicyFlag) -> None:
        self._values = frozenset((*self._values, flag))

    def __iter__(self) -> Iterator[PolicyFlag]:
        return iter(self._values)

    def iter(self) -> Iterable[PolicyFlag]:
        return iter(self._values)

    def to_vec(self) -> tuple[PolicyFlag, ...]:
        return tuple(self._values)

    @classmethod
    def defaults_for_drain(cls, drain: SessionDrain) -> PolicyFlagSet:
        return cls(DEFAULT_POLICY_FLAGS.get(drain, ()))

    @classmethod
    def from_identifiers(cls, identifiers: Iterable[str] | None) -> PolicyFlagSet:
        if not identifiers:
            return cls()
        values: list[PolicyFlag] = []
        for identifier in identifiers:
            try:
                values.append(PolicyFlag(identifier))
            except ValueError:
                continue
        return cls(values)


DEFAULT_POLICY_FLAGS: dict[SessionDrain, tuple[PolicyFlag, ...]] = {
    SessionDrain.PLANNING: (PolicyFlag.NO_EDIT,),
    SessionDrain.ANALYSIS: (PolicyFlag.NO_EDIT,),
    SessionDrain.DEVELOPMENT_ANALYSIS: (PolicyFlag.NO_EDIT,),
    SessionDrain.REVIEW_ANALYSIS: (PolicyFlag.NO_EDIT,),
    SessionDrain.REVIEW: (PolicyFlag.NO_EDIT,),
    SessionDrain.DEVELOPMENT: (PolicyFlag.ALLOW_SHELL,),
    SessionDrain.FIX: (PolicyFlag.ALLOW_SHELL,),
    SessionDrain.COMMIT: (PolicyFlag.ALLOW_GIT_WRITE,),
}

__all__ = ["DEFAULT_POLICY_FLAGS", "PolicyFlagSet"]
