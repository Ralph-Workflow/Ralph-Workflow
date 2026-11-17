from __future__ import annotations


class ToolBridgeError(Exception):
    pass


class ToolDispatchError(ToolBridgeError):
    pass


class ToolRegistrationError(ToolBridgeError):
    pass
