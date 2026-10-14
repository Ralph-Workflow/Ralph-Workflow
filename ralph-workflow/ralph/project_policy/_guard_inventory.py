"""Guard-inventory parser and claim-scope validator (v5, B7 / B9).

The v5 plan introduces a per-starter ``## Guard inventory`` markdown
table whose header and column grammar are pinned in
:mod:`ralph.project_policy.markers`. The parser lives in its own
module to keep :mod:`ralph.project_policy._content_checks` under
the repository's 1000-line cap. The parser exposes a small,
deterministic API:

* :func:`_parse_guard_inventory` returns the data rows of one
  starter's table (or ``[]`` when the section is missing or
  malformed).
* :func:`_check_guard_inventory` runs the validator check and
  returns a list of :class:`PolicyFinding` objects. The check
  emits one finding for a missing section, one for a malformed
  table, and per-row findings for empty cells and broad-guarantee
  phrases (the claim-scope rule, B7).
"""

from __future__ import annotations

import re
from typing import Final

from ralph.project_policy import markers
from ralph.project_policy.models import PolicyFinding

_MIN_TABLE_ROW_LENGTH = 2
_MIN_TABLE_LINES = 3


def _split_table_row(line: str) -> list[str] | None:
    """Split a markdown table row into cells, requiring balanced pipes.

    A leading pipe and unescaped trailing pipe are strictly required.
    Backslash-pipe (``\\|``) is preserved as a literal pipe in the cell value.
    If the row lacks a leading pipe or a trailing unescaped pipe, returns
    ``None``. Empty cells (``||``) become empty strings; whitespace-only
    cells are returned as whitespace strings so caller can flag empty-cell
    violations.
    """
    stripped = line.strip()
    if (
        len(stripped) < _MIN_TABLE_ROW_LENGTH
        or not stripped.startswith("|")
        or not stripped.endswith("|")
    ):
        return None
    # Check that trailing pipe is not escaped by an odd number of backslashes.
    num_backslashes = 0
    k = len(stripped) - 2
    while k >= 0 and stripped[k] == "\\":
        num_backslashes += 1
        k -= 1
    if num_backslashes % 2 == 1:
        return None

    inner = stripped[1:-1]
    cells: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\":
            if i + 1 < len(inner):
                next_ch = inner[i + 1]
                if next_ch == "\\":
                    current.append("\\")
                    i += 2
                    continue
                if next_ch == "|":
                    current.append("|")
                    i += 2
                    continue
            current.append(ch)
            i += 1
            continue
        if ch == "|":
            cells.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    cells.append("".join(current))
    return cells


def _is_table_separator(line: str, expected_columns: int = 5) -> bool:
    """Return True when ``line`` is a markdown table separator (a row of
    pipes, dashes, and optional colons) with exactly ``expected_columns`` cells."""
    stripped = line.strip()
    if (
        len(stripped) < _MIN_TABLE_ROW_LENGTH
        or not stripped.startswith("|")
        or not stripped.endswith("|")
    ):
        return False
    num_backslashes = 0
    k = len(stripped) - 2
    while k >= 0 and stripped[k] == "\\":
        num_backslashes += 1
        k -= 1
    if num_backslashes % 2 == 1:
        return False
    inner = stripped[1:-1]
    cells = inner.split("|")
    if len(cells) != expected_columns:
        return False
    for raw_cell in cells:
        stripped_cell = raw_cell.strip()
        if not stripped_cell:
            return False
        if not re.fullmatch(r":?-+:?", stripped_cell):
            return False
    return True


def _extract_table_lines(content: str) -> list[str] | None:
    """Extract consecutive non-empty lines from the guard inventory section."""
    lines = content.splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if line.strip() == markers.GUARD_INVENTORY_HEADING:
            start = idx
            break
    if start == -1:
        return None

    section_lines: list[str] = []
    for line in lines[start + 1 :]:
        if line.lstrip().startswith("#"):
            break
        section_lines.append(line)

    table_lines = [line.strip() for line in section_lines if line.strip()]
    if len(table_lines) < _MIN_TABLE_LINES:
        return None
    return table_lines


def _parse_guard_inventory(content: str) -> list[dict[str, str]]:
    """Parse the ``## Guard inventory`` table from a starter / project policy.

    Returns one dict per data row, with the six canonical keys
    (``guard``, ``owner``, ``protected_outcome``, ``detected_condition``,
    ``claim_scope``, ``retirement_trigger``) lowercased to canonical form. The header
    row is matched case-insensitively against
    :data:`markers.GUARD_INVENTORY_HEADER`; leading and trailing pipes
    are required, and pipes must be balanced.
    The function returns an empty list when the section is missing,
    when the table has no header, when the header doesn't match,
    when separators or trailing/leading pipes are missing, or when
    any row is malformed — the caller emits a single RWP-INVENTORY:*
    finding in those cases so malformed tables fail closed.
    """
    table_lines = _extract_table_lines(content)
    if table_lines is None:
        return []

    canonical = [h.lower() for h in markers.GUARD_INVENTORY_HEADER]

    # 1. Header row & 2. Separator row
    header_cells = _split_table_row(table_lines[0])
    valid_header = (
        header_cells is not None
        and len(header_cells) == len(canonical)
        and [c.strip().lower() for c in header_cells] == canonical
    )
    valid_sep = _is_table_separator(table_lines[1], expected_columns=len(canonical))
    if not (valid_header and valid_sep):
        return []

    # 3. Data rows: each must have leading/trailing pipe and balanced 6 columns
    out: list[dict[str, str]] = []
    for line in table_lines[2:]:
        cells = _split_table_row(line)
        if cells is None or len(cells) != len(canonical):
            return []  # malformed row; fail closed for whole table
        out.append(
            {
                "guard": cells[0].strip(),
                "owner": cells[1].strip(),
                "protected_outcome": cells[2].strip(),
                "detected_condition": cells[3].strip(),
                "claim_scope": cells[4].strip(),
                "retirement_trigger": cells[5].strip(),
            }
        )
    return out


def _starts_with_heading(content: str, heading: str) -> bool:
    """Return True if ``content`` carries the ``## Heading`` line."""
    return any(line.strip() == heading for line in content.splitlines())


# Sections that are excluded from the ratchet-linkage scan (frozen by
# the v5 plan to the three the plan enumerates). The rule only applies
# to BODY prose declarations, not the inventory table itself, the
# ``## Maintenance triggers`` section, or the ``## Research basis``
# section (both of which legitimately use the ratchet phrases when
# describing prior decisions or sources). Other sections (e.g. Portfolio
# ownership, Living document contract, Ralph markers) MUST NOT be
# excluded — a future edit that adds a ratchet to one of them MUST be
# caught by the linkage scan.
_EXCLUDED_SECTION_HEADINGS: Final[tuple[str, ...]] = (
    "## Guard inventory",
    "## Maintenance triggers",
    "## Research basis",
)


#: HTML-comment prefix pattern. The ratchet scan ignores any line whose
#: first non-whitespace is ``<!--`` because REPLACE-ME instructions
#: and remediation prompts use the bare ``ratchet`` token when
#: describing the rule (e.g. "the ratchet then holds that tighter
#: number") — those are template instructions, not policy
#: declarations.
_HTML_COMMENT_PREFIX: Final[str] = "<!--"


def _is_html_comment_line(line: str) -> bool:
    """Return True if ``line`` is an HTML comment (template instruction)."""
    return line.lstrip().startswith(_HTML_COMMENT_PREFIX)


#: Maximum number of trailing non-blank, non-heading, non-comment
#: lines the ratchet scan examines for a trailing
#: ``<!-- ralph-control: <id> -->`` token after a ratchet phrase.
#: The shipped corpus wraps the verification-policy ratchet across
#: four lines (ratchet phrase on line 1, sentence body on lines
#: 2-4, control token on line 5); 6 lines of look-ahead covers
#: that wrap with one-line headroom. Look-ahead is bounded on the
#: OTHER end by the sentence boundary: the scan STOPS as soon as
#: it crosses a sentence-ending line, a blank line, or a heading,
#: so a control token on a different sentence cannot be borrowed
#: (wt-15-policy analysis feedback).
_RATCHET_CONTROL_TOKEN_LOOKAHEAD_LINES: Final[int] = 6

#: Regex matching a sentence-ending punctuation character. The
#: ratchet scan refuses to look ahead beyond a sentence boundary,
#: so a control token on a different sentence cannot be borrowed
#: by a ratchet phrase (wt-15-policy analysis feedback).
_SENTENCE_ENDING_PUNCT_RE: Final[str] = r"[.!?]"
_SENTENCE_ENDING_PUNCT_PATTERN: Final[re.Pattern[str]] = re.compile(_SENTENCE_ENDING_PUNCT_RE)

#: Regex matching a real sentence boundary on a single visible
#: line: one or more ``.!?`` then whitespace then a capital
#: letter. The earlier punctuation-counting heuristic wrongly
#: counted a filename dot (``items.py the file``) or a bare
#: period at the end of a sentence as a new sentence; the
#: capital-letter lookahead is the standard "new sentence" cue
#: and is also why a tagged ratchet followed by an unrelated
#: next sentence is no longer treated as two declarations.
_SENTENCE_BOUNDARY_RE: Final[re.Pattern[str]] = re.compile(r"[.!?]+\s+(?=[A-Z])")


def _count_ratchet_phrases(line: str, phrase_re: re.Pattern[str]) -> int:
    """Return the number of distinct ratchet phrase occurrences on ``line``.

    Used to detect two-ratchets-on-one-line shapes that a single
    control token cannot legitimately cover. A line whose ratchet
    phrase count exceeds the number of distinct control tokens
    emitted on or before it is rejected; this is the wt-15-policy
    analysis-feedback fix that tightens the prior 6-line
    look-ahead.
    """
    return sum(1 for _ in phrase_re.finditer(line))


def _line_ends_sentence(line: str) -> bool:
    """Return True when ``line`` ends a sentence (trailing ``.``/``!``/``?``).

    The ratchet scan uses this to refuse look-ahead past the end
    of the ratchet sentence, so a control token on a different
    next sentence cannot be borrowed (wt-15-policy analysis
    feedback).
    """
    stripped = line.rstrip()
    if not stripped:
        return False
    # Strip a trailing HTML comment so the ratchet sentence
    # ``Sentence. <!-- ralph-control: id -->`` is treated as
    # ending a sentence, not as ``open``.
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
    """Split a visible (HTML-comment-stripped) line into sentence strings.

    The split is bounded by :data:`_SENTENCE_BOUNDARY_RE`: one or
    more ``.!?`` then whitespace then a capital letter. The result
    preserves the original whitespace inside each segment; the
    caller is responsible for stripping leading/trailing whitespace
    when it compares ratchet-phrase presence. Filename dots
    (``items.py the file``) and a bare trailing period without a
    following capital are NOT boundaries, so a single-sentence
    ``the audit's foo.bar.txt count`` stays a single segment.
    """
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
    """Return ``(declaration_text, control_id_or_empty)`` per body ratchet.

    A body ratchet declaration is a sentence in the starter body
    (excluding the Guard inventory table, the three
    :data:`_EXCLUDED_SECTION_HEADINGS` sections, and HTML comments
    which carry template instructions, not policy declarations) that
    contains any phrase from :data:`markers.RATCHET_DECLARATION_PHRASES`
    (case-insensitive). The v5 plan's per-sentence granularity
    forbids paragraph-level grouping: a tagged sentence and an
    adjacent untagged ``MUST NOT increase`` sentence cannot share
    one row, so the scan is sentence-aware.

    A "sentence" here is the run of body text from one sentence
    boundary to the next: the run starts at the first line of a
    paragraph (after a blank line or heading) OR after a
    sentence-ending punctuation mark (``.``/``!``/``?``), and
    ends at the next sentence-ending punctuation, the next blank
    line, the next heading, or end-of-file. A sentence may
    wrap across multiple body lines (a numbered list item that
    has the ratchet phrase on line 1, the rationale on lines
    2-3, the control token on line 4); the per-line scan looks
    ahead up to :data:`_RATCHET_CONTROL_TOKEN_LOOKAHEAD_LINES`
    non-blank, non-heading, non-comment lines for the trailing
    control token so a wrapped declaration is still linked to
    its row (wt-15-policy analysis feedback: the look-ahead is
    bounded by sentence boundaries so an unrelated next
    sentence's token cannot be borrowed).

    The function emits ONE declaration per ratchet sentence:
    multiple ratchet phrases in the same sentence share one
    control token. The empty-string ``control_id_or_empty``
    value flags an uncovered ratchet (rejected with stable id
    ``RWP-INVENTORY:RATCHET-UNCOVERED``).

    When a single line carries ratchet phrases in two distinct
    sentences (the line contains two sentence-ending
    punctuation marks), the scan records each sentence as its
    own declaration; the same line MUST carry one control token
    per ratchet sentence, otherwise the second/third
    declarations are uncovered.

    The grammar is regex-deterministic and excludes the inventory
    table plus the three plan-frozen excluded sections plus HTML
    comments.
    """
    out: list[tuple[str, str]] = []
    in_excluded = False
    in_table = False
    in_comment = False
    control_re: re.Pattern[str] = re.compile(
        markers.CONTROL_TOKEN_RE
    )  # cast-policy: seam: compiled pattern, findall returns list[str] per typeshed
    phrase_alts = "|".join(re.escape(p) for p in markers.RATCHET_DECLARATION_PHRASES)
    # The phrases are not all word-shaped so we use a plain
    # case-insensitive substring match rather than the usual
    # ``\b...\b`` word-boundary form. Word boundaries on the
    # bare ``ratchet`` token would over-match against ``ratchets``
    # (legitimate plural usage in shipped starters); the substring
    # form fires on both the singular and the plural because both
    # refer to the same constraint.
    phrase_re = re.compile(rf"(?i)(?:{phrase_alts})")
    lines = content.splitlines()
    total = len(lines)
    # The current ratchet sentence under construction. Each
    # sentence contributes at most one declaration; the
    # declaration is recorded when the sentence closes (a
    # sentence-ending punctuation, a blank line, a heading, or
    # end-of-file).
    sentence_has_phrase = False
    sentence_token_id = ""

    def flush() -> None:
        """Emit a ratchet declaration for the current sentence, if any."""
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
        # Multi-line HTML comment: track state across lines so
        # the body lines of a ``<!-- ... -->`` block are skipped
        # even when they do not start with ``<!--``.
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
    """Update the ratchet-declaration state for one body line.

    The body-line scan is the inner loop of
    :func:`_ratchet_declarations`; extracted into its own
    function so the orchestrator stays under the ruff
    PLR0912 / PLR0915 branch / statement caps. The function
    applies the same per-line rules described in
    :func:`_ratchet_declarations` and returns the updated
    ``(sentence_has_phrase, sentence_token_id, out)`` triple.
    """
    line_has_phrase = bool(phrase_re.search(stripped))
    line_tokens: list[str] = control_re.findall(
        stripped
    )  # cast-policy: seam: typed local ensures list[str] without cast()
    visible_text = re.sub(r"<!--.*?-->", "", stripped, flags=re.DOTALL)
    ends_sentence = _line_ends_sentence(stripped)
    # Real sentence boundaries: punctuation + whitespace + capital
    # letter. The earlier punctuation-counting heuristic wrongly
    # counted a filename dot (``items.py the file``) or a bare
    # period at the end of a sentence as a new sentence; the
    # capital-letter lookahead is the standard "new sentence" cue
    # and is also why a tagged ratchet followed by an unrelated
    # next sentence is no longer treated as two declarations.
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
    # ``ends_sentence`` is True when the line's visible text ends
    # in ``.``/``!``/``?``. A bare filename dot at end of line
    # (e.g. ``audit.py``) does NOT count because the dot is not
    # the visible line's last char after ``_line_ends_sentence``
    # strips the trailing period only if it is a real sentence
    # terminator; the function does the right thing for both
    # ``audit.py`` and ``reviewed change.``.
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
    """Look ahead up to N lines for a ``<!-- ralph-control: <id> -->`` token.

    Returns the captured id when the token appears on the ratchet
    line or on one of the next non-blank, non-heading lines, OR
    when the same line is itself a control-token comment. Returns
    the empty string when no such token is found in the window.

    Look-ahead is strictly sentence-local: the scan refuses to
    step onto a different sentence. If the ratchet line itself
    ends a sentence (``.``/``!``/``?``), the look-ahead is
    disabled entirely because the next line, no matter what it
    carries, belongs to a different sentence (wt-15-policy
    analysis feedback). If the ratchet line does NOT end a
    sentence (the ratchet phrase is mid-sentence), the look-ahead
    may pass over continuation lines that themselves end a
    sentence, so a control token at the end of a multi-line
    ratchet sentence is still captured. The scan STOPS at blank
    lines, headings, and the maximum line count
    :data:`_RATCHET_CONTROL_TOKEN_LOOKAHEAD_LINES`.
    """
    lookahead_comment = initial_in_comment
    ratchet_ends_sentence = _line_ends_sentence(ratchet_line)
    # Track whether the ratchet sentence has ended. Once it has,
    # the look-ahead will only accept a tag-only line (an HTML
    # comment with no visible text) as a token source, because a
    # lone tag is the conventional way to declare the row's
    # control id at the end of a list item in shipped starters.
    ratchet_sentence_over = ratchet_ends_sentence
    for offset in range(1, _RATCHET_CONTROL_TOKEN_LOOKAHEAD_LINES + 1):
        next_index = index + offset
        next_stripped = lines[next_index].strip() if next_index < total else ""
        if not next_stripped or next_stripped.startswith("#"):
            return ""
        # A ``<!-- ralph-control: <id> -->`` line IS the control
        # token (its grammar is a single-line HTML comment), so
        # it always counts as a match even when other comments
        # would be skipped. A tag-only line is treated as part
        # of the ratchet declaration's tag, even when the ratchet
        # sentence has already ended (shipped pattern: a wrapped
        # ratchet sentence ending with a period, followed by a
        # lone tag line as the next non-blank, non-heading line).
        next_visible = re.sub(r"<!--.*?-->", "", next_stripped, flags=re.DOTALL).strip()
        is_tag_only_line = not next_visible
        if is_tag_only_line:
            token_match = control_re.search(next_stripped)
            return token_match.group(1) if token_match else ""
        # If the ratchet line itself ended a sentence, the next
        # line is a different sentence by definition. The
        # look-ahead is disabled to prevent token borrowing.
        # Same for any continuation line after the ratchet
        # sentence has ended (wt-15-policy analysis feedback:
        # a wrapped untagged ratchet must not borrow a token
        # from a later sentence on a non-period intermediate
        # line).
        if ratchet_ends_sentence or ratchet_sentence_over:
            return ""
        # Update the sentence-over state. A continuation line
        # that ends a sentence marks the ratchet sentence as
        # over; the next non-blank, non-heading line is either a
        # tag-only line (the ratchet's control id, found at the
        # top of the next iteration) or a new sentence (the
        # ``ratchet_sentence_over`` check above stops the
        # look-ahead).
        if _line_ends_sentence(next_stripped):
            ratchet_sentence_over = True
        if lookahead_comment:
            # A multi-line comment ends on a line whose only
            # end-of-comment marker is the trailing ``-->``; if
            # that marker is absent we stay inside the comment.
            lookahead_comment = not next_stripped.endswith("-->")
            continue
        if _is_html_comment_line(next_stripped):
            lookahead_comment = not next_stripped.endswith("-->")
    return ""


def _control_id_for_guard_row(guard_cell: str) -> str | None:
    """Return the leading control id from a Guard cell, or ``None``.

    The grammar allows a row's Guard cell to begin with a backticked
    id `` `<id>` `` (any text may follow). The function captures the
    first such id using :data:`markers.CONTROL_ID_RE`. Returns
    ``None`` when the cell does not begin with a backticked id so
    callers can distinguish a row with no linkage from a row whose
    control id is the empty string.
    """
    match = re.match(markers.CONTROL_ID_RE, guard_cell.strip())
    return match.group(1) if match else None


def _claim_scope_findings(
    filename: str,
    path: str,
    index: int,
    outcome: str,
    detected: str,
) -> list[PolicyFinding]:
    """Return per-phrase claim-scope findings for one row.

    Extracted from :func:`_check_guard_inventory` to keep the per-row
    branch readable; a broad phrase that does not also appear in the
    detected condition is rejected with a stable RWP-INVENTORY finding.
    """
    findings: list[PolicyFinding] = []
    for phrase in markers.CLAIM_SCOPE_PHRASES:
        if phrase not in outcome:
            continue
        if phrase in detected:
            continue
        findings.append(
            PolicyFinding(
                requirement_id=(f"{markers.ID_INVENTORY}:{filename}:row-{index}:claim-scope"),
                path=path,
                missing_evidence=(
                    f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                    f"protected_outcome carries broad phrase "
                    f"{phrase!r} that does not appear in detected_condition; "
                    "rewrite the outcome to name only what the detector observes"
                ),
                required_outcome=(
                    f"either narrow row {index} protected_outcome to a "
                    "claim about the detector's actual observation, or "
                    f"add the phrase {phrase!r} to detected_condition"
                ),
            )
        )
    return findings


_CLAIM_SCOPE_SENTENCE_BOUNDARY_RE: Final[re.Pattern[str]] = re.compile(r"[.!?]+\s+|\n+")


def _claim_scope_sentence_count(cell: str) -> int:
    """Return the number of sentences in a ``Claim scope`` cell.

    The v5 plan freezes the cell to a single sentence. A sentence
    boundary is sentence-ending punctuation (``.!?``) followed by
    whitespace, or a line break. Filename-like tokens such as
    ``items.py`` or ``foo.bar.txt`` do NOT count as boundaries because
    their dots are not followed by whitespace. Empty cells return zero.
    An unterminated final segment (e.g. ``Counts items. Second sentence``)
    is preserved and counted as a sentence.
    """
    stripped = cell.strip()
    if not stripped:
        return 0
    clean = re.sub(r"[.!?]+$", "", stripped).strip()
    if not clean:
        return 1
    raw_segments: list[str] = _CLAIM_SCOPE_SENTENCE_BOUNDARY_RE.split(
        clean
    )  # cast-policy: seam: typed local ensures list[str] without cast()
    segments: list[str] = [s.strip() for s in raw_segments if s.strip()]
    return max(1, len(segments))


def _check_guard_inventory(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate the ``## Guard inventory`` table per v5 contract (B9).

    Findings emitted (stable v5 contract):

    * ``RWP-INVENTORY:<file>:missing`` when the section is missing
      entirely.
    * ``RWP-INVENTORY:<file>:malformed`` when the header row does not
      match :data:`markers.GUARD_INVENTORY_HEADER`.
    * ``RWP-INVENTORY:<file>:row-<n>:empty-<column>`` per row with an
      empty cell in any of the five base columns (the ``Claim scope``
      column uses the stable :data:`CLAIM_SCOPE_MISSING_FINDING_ID`
      instead, see below).
    * ``RWP-INVENTORY:CLAIM-SCOPE-MISSING`` per row with an empty
      ``Claim scope`` cell. The plan freezes the v5 contract id;
      filename/row suffixes would break black-box tests that pin
      the id.
    * ``RWP-INVENTORY:CLAIM-SCOPE-MULTI-SENTENCE`` per row whose
      ``Claim scope`` cell is more than one sentence. The plan
      freezes the cell to a single sentence; multi-sentence prose
      is rejected with a stable id.
    * ``RWP-INVENTORY:<file>:row-<n>:claim-scope`` per row whose
      ``Protected outcome`` or ``Claim scope`` cell carries a
      broad-guarantee phrase from
      :data:`markers.CLAIM_SCOPE_PHRASES` that does not also appear
      in the row's ``Detected condition`` (the deterministic
      claim-scope rule, B7).
    * ``RWP-INVENTORY:RATCHET-UNCOVERED`` per body ratchet sentence
      that has no inline ``<!-- ralph-control: <id> -->`` token
      (filename suffix is intentionally absent so the id is stable
      across files).
    * ``RWP-INVENTORY:RATCHET-UNCOVERED:<id>`` per body ratchet
      sentence whose control id does not appear at the start of any
      Guard cell.
    * ``RWP-INVENTORY:CONTROL-ORPHAN:<id>`` per Guard cell control
      id that no body declaration names.

    The check returns an empty list when the table is well-formed:
    no findings means the starter is B9-compliant.
    """
    if not _starts_with_heading(content, markers.GUARD_INVENTORY_HEADING):
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_INVENTORY}:{filename}:missing",
                path=path,
                missing_evidence=(
                    f"file lacks a {markers.GUARD_INVENTORY_HEADING} section; "
                    "every applicable starter must enumerate its guards with "
                    "the pinned six-cell row"
                ),
                required_outcome=(
                    f"add a `{markers.GUARD_INVENTORY_HEADING}` section "
                    "with the pinned header row and one data row per guard "
                    "(Guard | Owner | Protected outcome | Detected "
                    "condition | Claim scope | Retirement trigger)"
                ),
            )
        ]
    rows = _parse_guard_inventory(content)
    if not rows:
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_INVENTORY}:{filename}:malformed",
                path=path,
                missing_evidence=(
                    f"{markers.GUARD_INVENTORY_HEADING} section is present "
                    "but the table is empty or its header row does not "
                    "match the pinned grammar"
                ),
                required_outcome=(
                    f"fix the table to match the pinned header "
                    f"({' | '.join(markers.GUARD_INVENTORY_HEADER)}) and "
                    "include at least one data row"
                ),
            )
        ]
    findings: list[PolicyFinding] = []
    row_control_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        for column in (
            "guard",
            "owner",
            "protected_outcome",
            "detected_condition",
            "retirement_trigger",
        ):
            if row[column]:
                continue
            findings.append(
                PolicyFinding(
                    requirement_id=(
                        f"{markers.ID_INVENTORY}:{filename}:row-{index}:empty-{column}"
                    ),
                    path=path,
                    missing_evidence=(
                        f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                        f"has an empty {column!r} cell"
                    ),
                    required_outcome=(
                        f"fill the {column!r} cell of row {index} with a "
                        "non-empty value (use `none` only when the "
                        "column genuinely has no value to record)"
                    ),
                )
            )
        # Stable v5 contract: an empty ``Claim scope`` cell emits the
        # plan-frozen finding id ``RWP-INVENTORY:CLAIM-SCOPE-MISSING``
        # (no filename/row suffix). A multi-sentence ``Claim scope``
        # cell emits ``RWP-INVENTORY:CLAIM-SCOPE-MULTI-SENTENCE`` for
        # the same reason.
        if not row["claim_scope"]:
            findings.append(
                PolicyFinding(
                    requirement_id=markers.CLAIM_SCOPE_MISSING_FINDING_ID,
                    path=path,
                    missing_evidence=(
                        f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                        "has an empty 'Claim scope' cell; every row MUST "
                        "name the narrow claim its detector supports"
                    ),
                    required_outcome=(
                        f"fill the 'Claim scope' cell of row {index} with a "
                        "single-sentence statement of what the detector "
                        "actually observes"
                    ),
                )
            )
        elif (
            _claim_scope_sentence_count(row["claim_scope"])
            > markers._GUARD_INVENTORY_CLAIM_SCOPE_MAX_SENTENCES
        ):
            findings.append(
                PolicyFinding(
                    requirement_id=markers.CLAIM_SCOPE_MULTI_SENTENCE_FINDING_ID,
                    path=path,
                    missing_evidence=(
                        f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                        "has a multi-sentence 'Claim scope' cell; the v5 "
                        "plan freezes the cell to a single sentence so the "
                        "claim stays narrow and observable"
                    ),
                    required_outcome=(
                        f"rewrite the 'Claim scope' cell of row {index} as "
                        "exactly one sentence naming what the detector "
                        "actually observes"
                    ),
                )
            )
        # Apply the deterministic claim-scope rule to each row's
        # ``Protected outcome`` AND ``Claim scope`` cells. The same
        # predicate fires on a broad phrase absent from the row's
        # ``Detected condition`` for both cells.
        outcome = row["protected_outcome"].lower()
        scope = row["claim_scope"].lower()
        detected = row["detected_condition"].lower()
        findings.extend(_claim_scope_findings(filename, path, index, outcome, detected))
        findings.extend(_claim_scope_findings(filename, path, index, scope, detected))
        # Track the row's leading control id, if any, for the
        # orphan / uncovered checks below. A row without a leading
        # backticked id does NOT participate in the linkage scan.
        control_id = _control_id_for_guard_row(row["guard"])
        if control_id is not None:
            row_control_ids.add(control_id)

    # Body ratchet declarations: every body sentence carrying one of
    # the declared ratchet phrases MUST carry an inline control id,
    # and the id MUST appear as a backticked control id at the start
    # of some Guard cell. A body ratchet without a matching row is
    # RATCHET-UNCOVERED (stable v5 contract id); a row's control id
    # with no body declaration is CONTROL-ORPHAN.
    declarations = _ratchet_declarations(content)
    declared_ids = {control_id for _, control_id in declarations if control_id}
    for declaration_text, control_id in declarations:
        if not control_id:
            findings.append(
                PolicyFinding(
                    requirement_id=markers.RATCHET_UNCOVERED_FINDING_ID,
                    path=path,
                    missing_evidence=(
                        f"body ratchet sentence is not covered: {declaration_text!r}; "
                        "every body sentence naming a ratcheted / ratchet / "
                        "MUST NOT grow / MUST NOT increase constraint MUST end "
                        "with an inline ``<!-- ralph-control: <id> -->`` token "
                        "whose id appears at the start of a Guard inventory cell"
                    ),
                    required_outcome=(
                        "append ``<!-- ralph-control: <id> -->`` to the declaring "
                        "sentence and add a Guard row whose cell starts with the "
                        "matching backticked id `` `<id>` ``"
                    ),
                )
            )
            continue
        if control_id not in row_control_ids:
            findings.append(
                PolicyFinding(
                    requirement_id=(f"{markers.RATCHET_UNCOVERED_FINDING_ID}:{control_id}"),
                    path=path,
                    missing_evidence=(
                        f"body ratchet sentence carries control id {control_id!r} "
                        "but no Guard inventory row begins with that id; the "
                        "declaration is uncovered"
                    ),
                    required_outcome=(
                        f"add a Guard row whose cell starts with `{control_id}` "
                        "so the ratchet declaration is linked to its row"
                    ),
                )
            )
    findings.extend(
        PolicyFinding(
            requirement_id=(f"{markers.CONTROL_ORPHAN_FINDING_ID}:{orphan_id}"),
            path=path,
            missing_evidence=(
                f"Guard inventory row carries control id {orphan_id!r} but no "
                "body ratchet sentence names that id; the row is orphaned"
            ),
            required_outcome=(
                f"add a body ratchet sentence whose inline token is "
                f"``<!-- ralph-control: {orphan_id} -->``, or remove the "
                "backticked id from the Guard cell"
            ),
        )
        for orphan_id in sorted(row_control_ids - declared_ids)
    )
    return findings


__all__ = [
    "_check_guard_inventory",
    "_control_id_for_guard_row",
    "_parse_guard_inventory",
    "_ratchet_declarations",
]
