from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal, Self

from pydantic import ConfigDict

if TYPE_CHECKING:

    class RalphBaseModel:
        model_config: ClassVar[ConfigDict]
        model_fields: ClassVar[dict[str, object]]

        def __init__(self, /, **data: object) -> None: ...

        @classmethod
        def model_construct(
            cls: type[Self],
            _fields_set: set[str] | None = None,
            **values: object,
        ) -> Self: ...

        @classmethod
        def model_validate(
            cls: type[Self],
            obj: object,
        ) -> Self: ...

        @classmethod
        def model_validate_json(
            cls: type[Self],
            json_data: str | bytes | bytearray,
        ) -> Self: ...

        @classmethod
        def model_rebuild(
            cls,
            *,
            _types_namespace: dict[str, object] | None = None,
        ) -> bool | None: ...

        @classmethod
        def model_json_schema(
            cls,
            *,
            by_alias: bool = True,
            mode: Literal["validation", "serialization"] = "validation",
        ) -> dict[str, object]: ...

        def model_dump(
            self,
            *,
            mode: Literal["json", "python"] | str = "python",
            exclude_unset: bool = False,
            exclude_defaults: bool = False,
            exclude_none: bool = False,
        ) -> dict[str, object]: ...

        def model_dump_json(
            self,
            *,
            indent: int | None = None,
        ) -> str: ...

        def model_copy(
            self: Self,
            *,
            update: dict[str, object] | None = None,
        ) -> Self: ...
else:
    from pydantic import BaseModel as RalphBaseModel

__all__ = ["ConfigDict", "RalphBaseModel"]
