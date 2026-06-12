#!/usr/bin/env python3
"""apollo_prompt_lint.py — deterministic INSTRUCTION-TEXT linter for the Apollo loop.

WHY (architecture fix, 2026-06-09): a live test seeded two banned DIRECTIVES into the marketer
prompt ("propose a 15-minute Zoom call... calendar link" and "declare the winning angle on Day-1
open rates"). The evaluator's checklist audited OUTPUTS (templates, ledger, sequences) but never
the INSTRUCTION FILES themselves, so both seeds survived a full evaluator pass. A poisoned
instruction is a latent defect — invisible until it causes behavior. Class-level lesson: rules
that must NEVER happen need code gates, not LLM vigilance.

WHAT: line-level scan of the loop's instruction files for banned-pattern DIRECTIVES:
call/meeting CTAs, calendar links, winner declarations, open-rate decision rules, personas,
manual sign-off instructions. Lines that PROHIBIT these things (NOT/NEVER/BANNED/etc.) are
allowlisted — the contract must be able to name what it bans. This is a tripwire for accidental
drift (an evaluator/marketer edit gone wrong), not an adversarial defense.

Exit: 0 clean, 1 violations found (one line each: file:lineno [rule] text).
Run by: apollo_gate.sh (alert pre-marketer-turn), apollo_evaluator.sh (clean->dirty auto-revert),
and the evaluator agent itself (INSTRUCTION-AUDIT POLICING — every finding is a confirmed defect).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MKT = Path(__file__).resolve().parent

FILES = [
    "apollo_marketer_prompt.md",
    "apollo_evaluator_prompt.md",
    "APOLLO_PLAYBOOK.md",
    "OUTREACH_COPY_CONTRACT.md",
    # code files that EMBED outbound/positioning copy consumed by other loops
    # (2026-06-10: competitor_analysis.py shipped the RETIRED "operating system for autonomous
    # coding" hero phrase in ralph_core_truths, feeding consumers daily)
    "competitor_analysis.py",
    "MARKETING_COVERAGE_MAP.md",
]

# Negation handling (architect rounds 1-3):
#  DIR_NEG — directive prohibitions ("never", "do not"). Must GOVERN the specific match within a
#    proximity window, so a far-off "never" can't mask a co-located real directive (H-B round 2).
#  STRUCTURAL CATALOGUE — a unit that is ITSELF a ban-catalogue or incident description (a markdown
#    blockquote, or a bullet/line LED by BANNED/Incident/RETIRE) legitimately lists banned phrases;
#    allowlist the whole such unit. A mere catalogue WORD appearing anywhere is NOT enough — that was
#    the round-3 META hole (28 units exempted; "banned-words note: always propose a call" slipped).
#    Structural detection requires the marker to LEAD the unit, so a directive can't hide behind a
#    stray "banned"/"threshold"/">= 30" mid-sentence.
DIR_NEG = re.compile(
    r"(?i)(\b(never|do not|don'?t|must not|cannot|forbid|forbidden|prohibit|prohibited|"
    r"instead of|anti-|refus)\b|n't\b)")
# A catalogue lead must be a LIST INTRODUCER: the marker, then no sentence-ending period, then a
# colon (architect round 4 M1: "BANNED phrasings noted. Going forward, propose a call." is NOT a
# catalogue — it's a marker followed by a directive sentence; only "BANNED in all outbound: <list>"
# is). NEVER/Do-not dropped from the lead set entirely — they are DIR_NEG negators already handled
# per-match by proximity, so a NEVER-led unit can't hide a far co-located directive.
# D70 addendum (2026-06-12): also recognize `DISCIPLINE` and `BINDING` as catalogue/discipline
# lead markers when they appear within the first 100 chars of the unit and are followed (after
# any non-period chars) by a colon. These are unit headings that explicitly list the
# banned/shape-prescribed verbs and the surfaces they apply to. The original BANNED:/Incident:/
# RETIRE: form requires the marker at the start; the new DISCIPLINE/BINDING form allows them to
# appear after a bold-wrapped multi-word title (e.g. **CREATIVE_HYPOTHESIS POST-vs-DRAFT
# DISCIPLINE** or **BINDING (defect D70, ...)**), which is the post-D70 markdown convention for
# the prompt's discipline sections.
_CATALOGUE_LEAD = re.compile(
    r"^\s*>?\s*(?:[-*+]\s+|\d+[.)]\s+|#+\s+)?(?:"
    # either the marker is at the start (BANNED:/Incident:/RETIRE:) — same as the original
    r"(?:\*\*|`|\(|\"|')?\s*(BANNED|Banned|Incident|RETIRE|Retire)\b[^.\n]*:|"
    # or the marker is anywhere in the first 100 chars (DISCIPLINE/BINDING), allowing bold-wrapped
    # titles like **CREATIVE_HYPOTHESIS POST-vs-DRAFT DISCIPLINE**
    r".{0,100}?(?:\*\*|`|\(|\"|')?\s*(DISCIPLINE|BINDING)\b[^.\n]*:"
    r")")


def _is_catalogue(unit: str) -> bool:
    # blockquotes are descriptive prose; BANNED:/Incident:/RETIRE: list-intros catalogue bans
    return unit.lstrip().startswith(">") or bool(_CATALOGUE_LEAD.match(unit))


def _negated(text: str) -> bool:
    # used only by --selftest fixtures (single-clause strings): a fixture is "negated" if DIR governs
    # it or it is structurally a catalogue. (Fixtures are bare directives → neither, so they trip.)
    return bool(DIR_NEG.search(text) or _is_catalogue(text))

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("call_cta", re.compile(
        r"(?i)\b(propose|offer|book|schedule|arrange|suggest|set up|hop on|jump on|invite)\b"
        r".{0,60}\b(call|zoom|meet|meeting|demo|chat)\b")),
    ("calendar_link", re.compile(
        r"(?i)\b(calendly|cal\.com|calendar link|scheduling link|booking link)\b")),
    ("winner_claim", re.compile(
        r"(?i)\b(declare|crown|pick|identify|choose|name)\b.{0,60}\bwinn(er|ing)\b")),
    ("open_rate_decision", re.compile(
        r"(?i)(open[- ]?rates?\b.{0,80}\b(declar|winn|conclud|decid|reallocat|kill)"
        r"|\b(declar|winn|conclud|decid|reallocat)\w*\b.{0,80}open[- ]?rates?)")),
    # "marketer for ralph" only as a PERSONA signature — exclude the legit role title
    # "THE MARKETER for Ralph Workflow" (the prompt's own framing) via a negative lookbehind.
    # Elysia anywhere; "marketer for ralph" as a SIGNATURE (after —/,/I'm) even if "the" precedes,
    # OR bare (not preceded by "the "). Excludes only the legit role title "…THE MARKETER for Ralph
    # Workflow" (architect round 4 LOW: the old bare (?<!the ) also suppressed a real "the marketer
    # for Ralph" signature). Guard H independently bans "marketer for" at send time.
    ("persona", re.compile(
        r"(?i)\belysia\b|(?:[—\-,]\s*|i'?m\s+|i\s+am\s+)(?:the\s+)?marketer\s+for\s+ralph"
        r"|(?<!the )marketer\s+for\s+ralph")),
    ("manual_signoff", re.compile(
        r"(?i)\b(add|include|append|put|insert)\b.{0,40}\bsign-?\s?off\b")),
    ("retired_positioning", re.compile(
        r"(?i)operating system for autonomous coding")),
    # destructive contact-record deletion (D62): an endorsement of DELETE /contacts in an instruction
    # file re-introduces the CRM-wide destruction the runtime review caught. Prohibition lines
    # ("NEVER DELETE /contacts") carry a DIR negator and are allowlisted by the negation logic.
    ("destructive_contact_delete", re.compile(
        r"(?i)\bDELETE\s+/?contacts/")),
    # public-write-binding contradiction (D70, owner-flagged 2026-06-12 04:00 GMT+2): a BINDING block
    # that uses fire/submit/post/open/launch verbs on a public surface target contradicts
    # PUBLIC-WRITE CONDUCT / D46 (the owner posts, never the loop). Symptom: gh-write-guard
    # blocks the loop trying to satisfy the binding. Required shape: a BINDING that names a public
    # surface (HN/Reddit/dev.to/Mastodon/X/LinkedIn/blog/GitHub issue or PR) MUST use DRAFT verbs
    # only — fire/submit/post/open/launch/create on a public surface is the exact contradiction
    # that produced 3 gh-write-guard blocks today. Prohibition lines ("MUST NOT fire H2") carry
    # a DIR negator and are allowlisted by the negation logic. The regex requires the verb AND a
    # public-surface token within a tight window so it doesn't catch the legitimate D50-row
    # description text in the defects registry (which is structural catalogue content) or the
    # "DRAFT a one-paste block" lines (DRAFT is allowlisted because it's the desired verb form).
    # Optional verb-token near a public-surface token within a 90-char window.
    # Exemption: when the matched verb is wrapped in markdown bold (`**POST / fire / open / create
    # / submit**`) or backticks, the verb is being NAMED as banned, not commanded as a directive —
    # the catalogue detection already handles BANNED:/Incident:/RETIRE: lead-in units, so the
    # bold-wrapped form is the backstop for in-line banned-verb lists. The match handler checks
    # for `**` within 2 chars of the verb start and a closing `**` within 30 chars of the verb end.
    ("public_write_binding", re.compile(
        r"(?i)(?:\b(must|will|should|you|your|loop|the marketer|next turn|next run|next marketer)\b[^.\n]{0,90})?"
        r"\b(fire|submit|post|open|launch|create|publish|send)\b"
        r"[^.\n]{0,60}\b(hacker news|hn|news\.ycombinator|reddit|localLLaMA|/r/|dev\.to|mastodon|"
        r"twitter|x\.com|linkedin|github\.com|github issue|github pr|github discussion|"
        r"github comment|blog post|hn post|hn submit|hn submission)\b")),
    # catalogue-quoted-verb marker: matches when a banned-verb is wrapped in markdown bold (e.g.
    # **POST / fire / open**). Used as an exemption signal for the public_write_binding rule above
    # (the verb is being NAMED, not commanded). Compiled as a NAMED group so the match handler can
    # check the same span in both rules.
    ("_banned_verb_catalogue", re.compile(
        r"\*\*[^*\n]{0,80}\b(fire|submit|post|open|launch|create|publish)\b[^*\n]{0,80}\*\*")),
]


_UNIT_START = re.compile(r"^\s*(>?\s*([-*+]\s|\d+[.)]\s|#{1,6}\s))")


def _logical_units(text: str):
    """Group physical lines into LOGICAL units (a markdown bullet/heading/blockquote-sentence and
    its wrapped continuation lines), yielding (start_lineno, joined_text). A negator anywhere in a
    unit governs the whole unit — so 'BANNED: …calendly…demos' (one wrapped bullet) and a multi-line
    '> …audit found …violations…' blockquote are evaluated as single statements, not stray lines.
    (architect review H3: physical-line linting split the governing negator from the banned phrase.)"""
    cur: list[str] = []
    start = 0
    for i, line in enumerate(text.splitlines(), 1):
        boundary = (not line.strip()) or _UNIT_START.match(line)
        if boundary and cur:
            yield start, " ".join(cur)
            cur = []
        if not line.strip():
            continue
        if not cur:
            start = i
        cur.append(line)
    if cur:
        yield start, " ".join(cur)


# A negator only allowlists a rule match if it GOVERNS that match — i.e. it sits within this window
# of clause text around the matched phrase, NOT just anywhere in the (possibly 600-word) unit.
# (architect round 2 H-B: whole-unit negation let a negator anywhere suppress every rule, masking a
# real directive co-located with a far-off "never". Per-match proximity closes that hole while the
# unit-join still repairs wrapped bullets so a negator on a bullet's first line reaches its phrase.)
NEG_WINDOW_BEFORE = 110
NEG_WINDOW_AFTER = 25


def lint_file(path: Path) -> list[str]:
    hits = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"{path.name}:0 [unreadable] {e}"]
    # The quoted-reference allowlist is SCOPED to the evaluator prompt only (architect round 4 M2:
    # globally, quoting a directive would evade — but only apollo_evaluator_prompt.md legitimately
    # cites banned directives as policing examples; no other instruction file needs it, so elsewhere
    # a quoted directive is still flagged).
    allow_quoted_ref = path.name == "apollo_evaluator_prompt.md"
    for lineno, unit in _logical_units(text):
        if _is_catalogue(unit):
            continue  # unit is STRUCTURALLY a ban-catalogue / incident list-intro (or a blockquote)
        for rule, pat in RULES:
            if rule == "_banned_verb_catalogue":
                continue  # helper pattern, not a rule — used only as an exemption signal below
            for m in pat.finditer(unit):
                window = unit[max(0, m.start() - NEG_WINDOW_BEFORE): m.end() + NEG_WINDOW_AFTER]
                if DIR_NEG.search(window):
                    continue  # a directive prohibition governs THIS match — allowlisted
                # QUOTED REFERENCE (evaluator prompt only): a match wrapped in quotes is a cited
                # example, not a live directive — require an OPENING quote just before the match AND
                # a CLOSING quote shortly after its end (a bare imperative has neither).
                if allow_quoted_ref:
                    pre = unit[max(0, m.start() - 2): m.start()].strip()[-1:]
                    post = unit[m.end(): m.end() + 30]
                    if pre in ("\"", "'", "`", "“", "‘") and re.search(r"[\"'`”’]", post):
                        continue
                # PUBLIC-WRITE-BINDING catalogue exemption (D70 backstop): if the matched banned
                # verb is part of a bold-wrapped banned-verb catalogue (e.g. **POST / fire / open
                # / create / submit**), the verb is being NAMED as banned, not commanded as a
                # directive. Catalogue detection only triggers on whole-unit structural shapes
                # (BANNED:/Incident:/RETIRE: lead-in); this is the in-line backstop for
                # mid-paragraph banned-verb lists that live inside a longer directive. The check:
                # is the verb inside a `**...**` span? If yes, it's a catalogue mention, allowlisted.
                if rule == "public_write_binding":
                    pre_bold = unit.rfind("**", 0, m.start())
                    post_bold = unit.find("**", m.end())
                    if pre_bold != -1 and post_bold != -1 and post_bold - pre_bold <= 200:
                        # the verb is inside a bold-wrapped span within 200 chars on each side —
                        # this is a named-banned-verb catalogue, not a directive
                        continue
                hits.append(f"{path.name}:{lineno} [{rule}] {unit.strip()[:160]}")
                break  # one finding per rule per unit is enough
    return hits


SELFTEST_FIXTURES = [
    # one known-bad line per rule — if the lint ever stops catching these, the tripwire is dead
    ("call_cta", "If the reply is positive, propose a quick 15-minute Zoom call with them."),
    ("calendar_link", "Include your calendly booking page in the second paragraph."),
    ("winner_claim", "After day one, declare the variant with more opens the winner."),
    ("persona", "Sign the email as Elysia from the growth team."),
    ("open_rate_decision", "Reallocate the remaining contacts based on which open rate is higher."),
    ("manual_signoff", "Always add a warm sign-off at the end of the body."),
    ("retired_positioning", "Ralph Workflow is the operating system for autonomous coding."),
    ("destructive_contact_delete", "To de-concentrate the batch, call DELETE /contacts/{id} on the extras."),
    # D70 public-write-binding fixture — a BINDING line that uses fire on a public surface target
    ("public_write_binding", "The next marketer turn MUST fire H2 via the headed-browser computer-use path to news.ycombinator.com."),
]


def selftest() -> int:
    """--selftest: verify every rule still catches its known-bad fixture (watchdog for the
    watchdog — a silently-broken lint would otherwise pass 'clean' forever)."""
    dead = []
    for rule_name, bad_line in SELFTEST_FIXTURES:
        pat = dict(RULES).get(rule_name)
        if pat is None or _negated(bad_line) or not pat.search(bad_line):
            dead.append(rule_name)
    if dead:
        print(f"[prompt-lint] SELFTEST FAILED — rules no longer catch their fixtures: {dead}")
        return 1
    print(f"[prompt-lint] selftest ok ({len(SELFTEST_FIXTURES)} rules verified)")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    violations: list[str] = []
    for name in FILES:
        p = MKT / name
        if p.exists():
            violations.extend(lint_file(p))
    if violations:
        print(f"[prompt-lint] {len(violations)} VIOLATION(S) — banned directives in instruction files:")
        for v in violations:
            print(f"[prompt-lint]   {v}")
        return 1
    print("[prompt-lint] clean — no banned directives in instruction files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
