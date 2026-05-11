"""First-party Pydantic typing compatibility helpers.

Ralph intentionally keeps strict mypy enabled without enabling the
``pydantic.mypy`` plugin. Some upstream Pydantic surfaces still expose ``Any``
in ways that trip ``disallow_any_explicit`` / ``disallow_any_expr`` when a
module subclasses ``pydantic.BaseModel`` directly.

``RalphBaseModel`` keeps runtime behavior identical to ``pydantic.BaseModel``
while providing an Any-free type-checking facade for the small subset of the
BaseModel API that Ralph actually relies on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal, Self

from pydantic import ConfigDict

if TYPE_CHECKING:
    class RalphBaseModel:
        """Any-free static facade for ``pydantic.BaseModel`` used by Ralph."""

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
        def model_validate(  # noqa: PLR0913
            cls: type[Self],
            obj: object,
            *,
            strict: bool | None = None,
            extra: Literal['allow', 'ignore', 'forbid'] | None = None,
            from_attributes: bool | None = None,
            context: object | None = None,
            by_alias: bool | None = None,
            by_name: bool | None = None,
        ) -> Self: ...

        @classmethod
        def model_validate_json(  # noqa: PLR0913
            cls: type[Self],
            json_data: str | bytes | bytearray,
            *,
            strict: bool | None = None,
            extra: Literal['allow', 'ignore', 'forbid'] | None = None,
            context: object | None = None,
            by_alias: bool | None = None,
            by_name: bool | None = None,
        ) -> Self: ...

        @classmethod
        def model_rebuild(
            cls,
            *,
            force: bool = False,
            raise_errors: bool = True,
            _parent_namespace_depth: int = 2,
            _types_namespace: dict[str, object] | None = None,
        ) -> bool | None: ...

        def model_dump(  # noqa: PLR0913
            self,
            *,
            mode: Literal['json', 'python'] | str = 'python',
            by_alias: bool | None = None,
            exclude_unset: bool = False,
            exclude_defaults: bool = False,
            exclude_none: bool = False,
            round_trip: bool = False,
            warnings: bool | Literal['none', 'warn', 'error'] = True,
        ) -> dict[str, object]: ...

        def model_dump_json(  # noqa: PLR0913
            self,
            *,
            indent: int | None = None,
            ensure_ascii: bool = False,
            by_alias: bool | None = None,
            exclude_unset: bool = False,
            exclude_defaults: bool = False,
            exclude_none: bool = False,
            round_trip: bool = False,
            warnings: bool | Literal['none', 'warn', 'error'] = True,
        ) -> str: ...

        def model_copy(
            self: Self,
            *,
            update: dict[str, object] | None = None,
            deep: bool = False,
        ) -> Self: ...
else:
    from pydantic import BaseModel as RalphBaseModel

__all__ = ['RalphBaseModel']
