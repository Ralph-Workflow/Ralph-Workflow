from __future__ import annotations

from .content_channel import ContentChannel, channel_of


def _parser_tagged_channel(parser_key: str, raw_line: str) -> ContentChannel | None:
    from ralph.agents.parsers import get_parser

    try:
        parser = get_parser(parser_key)
    except ValueError:
        return None
    classify_line = getattr(parser, "classify_line", None)
    if not callable(classify_line):
        return None
    for output_line in classify_line(raw_line):
        return output_line.channel
    return None


def line_channel(parser_key: str | None, raw_line: str) -> ContentChannel:
    if parser_key is not None:
        tagged = _parser_tagged_channel(parser_key, raw_line)
        if tagged is not None:
            return tagged
    return channel_of(raw_line)


__all__ = ["line_channel"]
