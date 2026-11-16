from __future__ import annotations


class ModelEntryValidationError(ValueError, TypeError):
    pass


__all__ = ["ModelEntryValidationError"]
