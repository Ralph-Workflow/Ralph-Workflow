from collections.abc import Iterable, Iterator

class tqdm[T]:

    def __init__(
        self,
        iterable: Iterable[T] | None = None,
        *,
        desc: str | None = None,
        unit: str = "it",
        leave: bool = True,
        file: object | None = None,
    ) -> None: ...
    def __iter__(self) -> Iterator[T]: ...
    def __next__(self) -> T: ...
    def close(self) -> None: ...
