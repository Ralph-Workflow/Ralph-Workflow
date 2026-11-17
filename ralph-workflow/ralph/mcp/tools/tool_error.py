from __future__ import annotations


class ToolError(Exception):
    pass


class CapabilityDeniedError(ToolError):
    pass


class InvalidParamsError(ToolError):
    pass
