from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke.attempt_supervision import AttemptSupervisor
from ralph.agents.parsers import resolve_parser_key
from ralph.agents.parsers.line_channel import line_channel
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from ralph.agents.parsers.content_channel import ContentChannel
    from ralph.config.models import AgentConfig
    from ralph.process.teardown import ProcessTeardown


def supervisor_process_teardown(
    handle: object,
    process_teardown: ProcessTeardown | None,
) -> AttemptSupervisor:
    return AttemptSupervisor(handle, process_teardown=process_teardown)


def reader_parser_key(config: AgentConfig) -> str | None:
    command_value: object = getattr(config, "cmd", None)
    json_parser_value: object = getattr(config, "json_parser", None)
    transport_value: object = getattr(config, "transport", None)
    command = command_value if isinstance(command_value, str) else None
    json_parser = json_parser_value if isinstance(json_parser_value, JsonParserType) else None
    transport = transport_value if isinstance(transport_value, AgentTransport) else None
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
