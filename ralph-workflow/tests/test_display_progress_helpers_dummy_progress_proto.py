from __future__ import annotations


class _DummyProgressProto:
    def __init__(self) -> None:
        self.add_calls: list[dict[str, object | None]] = []
        self.update_calls: list[dict[str, object | None]] = []

    def add_task(
        self,
        description: str,
        *,
        parent: int | None = None,
        total: int | None = None,
        completed: int = 0,
    ) -> int:
        self.add_calls.append(
            {
                "description": description,
                "parent": parent,
                "total": total,
                "completed": completed,
            }
        )
        return 88

    def __enter__(self) -> _DummyProgressProto:
        return self

    def __exit__(self, exc_type: object, exc: object, _tb: object) -> bool | None:
        return None

    def update(
        self,
        *,
        task_id: int,
        completed: int | None = None,
        advance: int | None = None,
        description: str | None = None,
    ) -> None:
        self.update_calls.append(
            {
                "task_id": task_id,
                "completed": completed,
                "advance": advance,
                "description": description,
            }
        )
