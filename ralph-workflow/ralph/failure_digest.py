from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import re


def _leading_lines(lines: list[str], *, max_lines: int, max_chars: int) -> list[str]:
    kept: list[str] = []
    size = 0
    for line in lines[:max_lines]:
        size += len(line) + 1
        if size > max_chars:
            break
        kept.append(line)
    return kept


def failure_line_digest(
    output: str,
    pattern: re.Pattern[str],
    *,
    half_lines: int,
    half_chars: int,
    line_chars: int,
) -> str:
    lines = [line.strip()[:line_chars] for line in output.splitlines() if pattern.search(line)]
    head = _leading_lines(lines, max_lines=half_lines, max_chars=half_chars)
    tail = _leading_lines(lines[len(head) :][::-1], max_lines=half_lines, max_chars=half_chars)[
        ::-1
    ]
    omitted = len(lines) - len(head) - len(tail)
    gap = [f"... {omitted} more matching lines ..."] if omitted else []
    return "\n".join((*head, *gap, *tail))


def bounded_failure_output(output: str, pattern: re.Pattern[str], *, limit: int) -> str:
    text = output.strip()
    if len(text) <= limit:
        return text
    digest = failure_line_digest(
        text, pattern, half_lines=limit // 160, half_chars=limit // 4, line_chars=400
    )
    tail_budget = max(0, limit - len(digest) - 80)
    chunk = text[-tail_budget:] if tail_budget else ""
    tail = chunk.partition("\n")[2] or chunk
    omitted = len(text) - len(tail)
    return (
        f"Failure lines:\n{digest}\n\n... {omitted} chars omitted; output tail ...\n{tail}"
        if digest
        else f"... {omitted} chars omitted; output tail ...\n{tail}"
    )


__all__ = ["bounded_failure_output", "failure_line_digest"]
