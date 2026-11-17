from collections.abc import Iterable

class _ExaSearchResponse:
    results: Iterable[object]

class Exa:
    def __init__(self, *, api_key: str) -> None: ...
    def search(self, query: str, *, num_results: int) -> _ExaSearchResponse: ...
