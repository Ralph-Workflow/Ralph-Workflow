from __future__ import annotations

from loguru import logger

from ralph._build_identity import build_identity

_IDENTITY_SUFFIX = " [build={}]"


def terminal_message(message: str) -> str:
    return f"{message}{_IDENTITY_SUFFIX}"


def critical(message: str, *args: object) -> None:
    logger.critical(terminal_message(message), *args, build_identity())


def terminal(message: str, *args: object) -> None:
    logger.info(terminal_message(message), *args, build_identity())
