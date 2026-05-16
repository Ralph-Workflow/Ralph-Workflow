"""Domain error for invalid planning work_units payloads."""


class WorkUnitsValidationError(ValueError):
    """Raised when a planning artifact contains invalid work_units."""


__all__ = ["WorkUnitsValidationError"]
