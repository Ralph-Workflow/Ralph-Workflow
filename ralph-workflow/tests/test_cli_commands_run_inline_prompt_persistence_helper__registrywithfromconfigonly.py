from __future__ import annotations


class _RegistryWithFromConfigOnly:
    called_with: object | None = None

    @classmethod
    def from_config(cls, config: object) -> _RegistryWithFromConfigOnly:
        cls.called_with = config
        return cls()

    def get(self, _name: str) -> object:
        return object()
