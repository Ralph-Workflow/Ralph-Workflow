from __future__ import annotations

import re
from typing import Final

from ralph.project_policy import markers

_EXCLUDED_SECTION_HEADINGS: Final[tuple[str, ...]] = (
    "## Guard inventory",
    "## Maintenance triggers",
    "## Research basis",
)


_HTML_COMMENT_PREFIX: Final[str] = "<!--"


def _is_html_comment_line(line: str) -> bool:
    return line.lstrip().startswith(_HTML_COMMENT_PREFIX)


_RATCHET_CONTROL_TOKEN_LOOKAHEAD_LINES: Final[int] = 6

_SENTENCE_ENDING_PUNCT_RE: Final[str] = r"[.!?]"
_SENTENCE_ENDING_PUNCT_PATTERN: Final[re.Pattern[str]] = re.compile(_SENTENCE_ENDING_PUNCT_RE)

_SENTENCE_BOUNDARY_RE: Final[re.Pattern[str]] = re.compile(r"[.!?]+\s+(?=[A-Z])")


def _count_ratchet_phrases(line: str, phrase_re: re.Pattern[str]) -> int:
    return sum(1 for _ in phrase_re.finditer(line))


def _line_ends_sentence(line: str) -> bool:
    stripped = line.rstrip()
    if not stripped:
        return False
    while True:
        end = stripped.rfind("-->")
        if end == -1 or end != len(stripped) - 3:
            break
        start = stripped.rfind("<!--", 0, end)
        if start == -1:
            break
        stripped = stripped[:start].rstrip()
    if not stripped:
        return False
    return bool(re.search(_SENTENCE_ENDING_PUNCT_RE, stripped[-1]))


def _split_visible_into_sentences(visible_text: str) -> list[str]:
    if not visible_text.strip():
        return [""]
    pieces: list[str] = []
    last_end = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(visible_text):
        pieces.append(visible_text[last_end : match.end()])
        last_end = match.end()
    pieces.append(visible_text[last_end:])
    return pieces


def _ratchet_declarations(content: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    in_excluded = False
    in_table = False
    in_comment = False
    control_re: re.Pattern[str] = re.compile(markers.CONTROL_TOKEN_RE)
    phrase_alts = "|".join(re.escape(p) for p in markers.RATCHET_DECLARATION_PHRASES)
    phrase_re = re.compile(rf"(?i)(?:{phrase_alts})")
    lines = content.splitlines()
    total = len(lines)
    sentence_has_phrase = False
    sentence_token_id = ""

    def flush() -> None:
        nonlocal sentence_has_phrase, sentence_token_id
        if sentence_has_phrase:
            out.append(("", sentence_token_id))
        sentence_has_phrase = False
        sentence_token_id = ""

    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            flush()
            in_excluded = stripped in _EXCLUDED_SECTION_HEADINGS
            in_table = stripped == markers.GUARD_INVENTORY_HEADING
            in_comment = False
            continue
        if in_comment:
            if stripped.endswith("-->") and not stripped.startswith(_HTML_COMMENT_PREFIX):
                in_comment = False
            continue
        if _is_html_comment_line(stripped):
            in_comment = not stripped.endswith("-->")
            continue
        if in_excluded or in_table:
            continue
        if not stripped:
            flush()
            continue
        sentence_has_phrase, sentence_token_id, out = _scan_body_line(
            stripped,
            lines,
            index,
            total,
            in_comment,
            control_re,
            phrase_re,
            sentence_has_phrase,
            sentence_token_id,
            out,
        )
    flush()
    return out


def _scan_body_line(
    stripped: str,
    lines: list[str],
    index: int,
    total: int,
    in_comment: bool,
    control_re: re.Pattern[str],
    phrase_re: re.Pattern[str],
    sentence_has_phrase: bool,
    sentence_token_id: str,
    out: list[tuple[str, str]],
) -> tuple[bool, str, list[tuple[str, str]]]:
    line_has_phrase = bool(phrase_re.search(stripped))
    line_tokens: list[str] = control_re.findall(stripped)
    visible_text = re.sub(r"<!--.*?-->", "", stripped, flags=re.DOTALL)
    ends_sentence = _line_ends_sentence(stripped)
    sentence_texts = _split_visible_into_sentences(visible_text)
    ratchet_sentence_indices = [
        i for i, text in enumerate(sentence_texts) if phrase_re.search(text)
    ]
    if ratchet_sentence_indices:
        sentence_has_phrase = True
        if not sentence_token_id and line_tokens:
            sentence_token_id = line_tokens[0]
            extra_tokens = line_tokens[1:]
        else:
            extra_tokens = line_tokens
        for offset, _ratchet_index in enumerate(ratchet_sentence_indices[1:]):
            if offset < len(extra_tokens):
                out.append(("", extra_tokens[offset]))
            else:
                out.append(("", ""))
    elif line_tokens and sentence_has_phrase and not sentence_token_id:
        sentence_token_id = line_tokens[0]
    if ends_sentence:
        if sentence_has_phrase:
            out.append(("", sentence_token_id))
        sentence_has_phrase = False
        sentence_token_id = ""
        return sentence_has_phrase, sentence_token_id, out
    if line_has_phrase and not sentence_token_id:
        token = _find_ratchet_control_token(lines, index, total, control_re, in_comment, stripped)
        if token:
            sentence_token_id = token
    return sentence_has_phrase, sentence_token_id, out


def _find_ratchet_control_token(
    lines: list[str],
    index: int,
    total: int,
    control_re: re.Pattern[str],
    initial_in_comment: bool,
    ratchet_line: str,
) -> str:
    lookahead_comment = initial_in_comment
    ratchet_ends_sentence = _line_ends_sentence(ratchet_line)
    ratchet_sentence_over = ratchet_ends_sentence
    for offset in range(1, _RATCHET_CONTROL_TOKEN_LOOKAHEAD_LINES + 1):
        next_index = index + offset
        next_stripped = lines[next_index].strip() if next_index < total else ""
        if not next_stripped or next_stripped.startswith("#"):
            return ""
        next_visible = re.sub(r"<!--.*?-->", "", next_stripped, flags=re.DOTALL).strip()
        is_tag_only_line = not next_visible
        if is_tag_only_line:
            token_match = control_re.search(next_stripped)
            return token_match.group(1) if token_match else ""
        if ratchet_ends_sentence or ratchet_sentence_over:
            return ""
        if _line_ends_sentence(next_stripped):
            ratchet_sentence_over = True
        if lookahead_comment:
            lookahead_comment = not next_stripped.endswith("-->")
            continue
        if _is_html_comment_line(next_stripped):
            lookahead_comment = not next_stripped.endswith("-->")
    return ""


def _control_id_for_guard_row(guard_cell: str) -> str | None:
    match = re.match(markers.CONTROL_ID_RE, guard_cell.strip())
    return match.group(1) if match else None


__all__ = [
    "_control_id_for_guard_row",
    "_ratchet_declarations",
]
