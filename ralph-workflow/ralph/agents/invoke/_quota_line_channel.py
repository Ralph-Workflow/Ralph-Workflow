from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.agents.invoke.attempt_supervision import AttemptSupervisor
from ralph.agents.parsers import resolve_parser_key
from ralph.agents.parsers.line_channel import line_channel

if TYPE_CHECKING:
    from ralph.agents.parsers.content_channel import ContentChannel
    from ralph.config.enums import AgentTransport
    from ralph.config.json_parser_type import JsonParserType
    from ralph.config.models import AgentConfig
    from ralph.process.teardown import ProcessTeardown


def supervisor_process_teardown(
    handle: object,
    process_teardown: ProcessTeardown | None,
) -> AttemptSupervisor:
    return AttemptSupervisor(handle, process_teardown=process_teardown)


def reader_parser_key(config: AgentConfig) -> str | None:
    command = cast("str | None", getattr(config, "cmd", None))
    json_parser = cast("JsonParserType | None", getattr(config, "json_parser", None))
    transport = cast("AgentTransport | None", getattr(config, "transport", None))
    if json_parser is None or transport is None:
        return None
    return resolve_parser_key(command or "", json_parser, transport)


def quota_evidence_channel(
    config: AgentConfig,
    raw_line: str,
    *,
    parser_key: str | None = None,
) -> ContentChannel:
    key = parser_key if parser_key is not None else reader_parser_key(config)
    return line_channel(key, raw_line)


__all__ = ["quota_evidence_channel", "reader_parser_key", "supervisor_process_teardown"]
