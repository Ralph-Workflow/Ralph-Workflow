from __future__ import annotations


class OutcomeLedger:
    def __init__(self) -> None:
        self.entries: dict[str, str] = {}

    def put(self, key: str, value: str) -> None:
        self.entries[key] = value

    def read(self, key: str) -> str:
        value = self.entries.get(key)
        if value is None:
            raise AssertionError(f"durable outcome ledger has no entry {key!r}")
        return value
