from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence


@runtime_checkable
class GuidelineSource(Protocol):
    @property
    def quality_checks(self) -> Sequence[str]: ...

    @property
    def security_checks(self) -> Sequence[str]: ...

    @property
    def performance_checks(self) -> Sequence[str]: ...

    @property
    def testing_checks(self) -> Sequence[str]: ...

    @property
    def documentation_checks(self) -> Sequence[str]: ...

    @property
    def idioms(self) -> Sequence[str]: ...

    @property
    def anti_patterns(self) -> Sequence[str]: ...

    @property
    def concurrency_checks(self) -> Sequence[str]: ...

    @property
    def resource_checks(self) -> Sequence[str]: ...

    @property
    def observability_checks(self) -> Sequence[str]: ...

    @property
    def secrets_checks(self) -> Sequence[str]: ...

    @property
    def api_design_checks(self) -> Sequence[str]: ...


__all__ = ["GuidelineSource"]
