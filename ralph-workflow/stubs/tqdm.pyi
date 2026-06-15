from collections.abc import Iterable, Iterator
from typing import Generic, TypeVar

_T = TypeVar("_T")


class tqdm(Generic[_T]):
    """Minimal stub for the tqdm progress-bar iterator."""

    def __init__(
        self,
        iterable: Iterable[_T] | None = None,
        *,
        desc: str | None = None,
        unit: str = "it",
        leave: bool = True,
        file: object | None = None,
    ) -> None: ...
    def __iter__(self) -> Iterator[_T]: ...
    def __next__(self) -> _T: ...
    def close(self) -> None: ...
