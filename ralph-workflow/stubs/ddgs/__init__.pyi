from collections.abc import Mapping

class DDGS:
    def text(
        self,
        query: str,
        *,
        max_results: int = ...,
    ) -> list[Mapping[str, object]] | None: ...
