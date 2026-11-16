from __future__ import annotations


class SchemaVersionTypeError(TypeError, RuntimeError):
    pass


__all__ = ["SchemaVersionTypeError"]
