"""Render-integrity audit for the packaged prompt templates.

Renders every top-level template under ``ralph/prompts/templates/*.jinja``
through the real rendering path (``TemplateContext.default()`` +
``ralph.prompts.template_engine.render_template``, i.e. the same registry,
partial set, and Jinja environment ``materialize.py`` uses) across the main
variable combinations and asserts five integrity properties on every
rendered prompt:

1. **No unrendered Jinja markers** — ``{{``, ``{%`` and ``{#`` must not
   survive into the rendered output.
2. **Include resolution** — every template x context combination must render
   without raising (``TemplateNotFound`` or any other
   ``TemplateRenderingError`` from the engine IS the failure signal; the
   audit's job is to exercise every combination).
3. **No duplicated markdown section headings** — a heading line
   (``#``/``##``/... outside fenced code blocks) must not appear twice in
   one rendered prompt.
4. **No verbatim duplicated text blocks** — no whitespace-normalized
   paragraph of >= 120 characters may appear twice within one rendered
   prompt (catches partial-vs-body restatement drift).
5. **No doubled-label defects** — no ``LABEL:`` line immediately followed
   by an identical ``LABEL:`` line. Runs of 3+ consecutive blank lines are
   reported as formatting warnings but do not fail the gate because removing
   them globally can mutate user-supplied payloads and fenced examples.

Rendered contexts: a baseline (all toggles off), one context per toggle
(``LAST_RETRY_ERROR``, ``ANALYSIS_FEEDBACK``, ``HAS_GIT_WRITE``,
``HIDE_ARTIFACT_SUBMISSION_GUIDANCE``), and an all-on context. Base
variables come from the real capability mapping
(``default_caps_and_flags_for_drain`` + ``capability_template_variables``
for the development drain); every remaining variable any template or
partial references (discovered via ``jinja2.meta``) is filled with a
distinct deterministic placeholder so ``StrictUndefined`` cannot mask an
unexercised branch. Cross-document duplication (master prompt vs template)
is out of scope — only single rendered prompts are inspected.

Deliberate-duplication allowlist (explicit, documented, minimal):
``worker_developer`` embeds the full base developer prompt via
``{{ base_prompt }}``; the wrapper is audited with a stub ``base_prompt``
so wrapper-vs-embedded-base duplication cannot fire while the wrapper's
own text stays fully checked. Any other entry added to
``deliberate_duplication_allowlist()`` must carry a one-line justification
comment.

Usage:
    python -m ralph.testing.audit_template_render_integrity

Exit 0 = clean, 1 = at least one integrity violation.
"""

from __future__ import annotations

import itertools
import re
import sys
from collections import Counter
from typing import cast

from jinja2 import Environment, meta

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import TemplateRenderingError, render_template
from ralph.prompts.template_registry import packaged_template_root
from ralph.prompts.template_variables import (
    capability_template_variables,
    default_caps_and_flags_for_drain,
)

_MIN_DUPLICATE_PARAGRAPH_CHARS = 120
#: Whitespace-normalized paragraphs at or above this length participate in
#: the verbatim-duplication check (check 4). Shorter fragments (bullet
#: fragments, labels) repeat legitimately.

_MAX_CONSECUTIVE_BLANK_LINES = 2
#: A run of more than this many consecutive blank lines in a rendered
#: prompt is a whitespace defect (check 5).

_DUPLICATION_FINGERPRINT_CHARS = 60
#: Duplicated headings/paragraphs are matched against the deliberate
#: duplication allowlist by their first this-many normalized characters.

_JINJA_MARKERS: tuple[str, ...] = ("{{", "{%", "{#")

_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_HEADING_RE = re.compile(r"^#{1,6} \S")

_ENGINE_GLOBAL_NAMES: frozenset[str] = frozenset({"raise_error"})
#: Names ``jinja2.meta`` reports as undeclared that are actually engine
#: globals installed by ``render_template``; they must not receive
#: placeholder values (calling a string placeholder would break rendering).

_TOGGLE_ON_VALUES: tuple[tuple[str, str], ...] = (
    ("LAST_RETRY_ERROR", "Previous submission was rejected; fix the reported error and retry."),
    ("ANALYSIS_FEEDBACK", "Analysis feedback: address finding F-1 before resubmitting."),
    ("HAS_GIT_WRITE", "true"),
    ("HIDE_ARTIFACT_SUBMISSION_GUIDANCE", "true"),
)
#: The main context toggles and their "on" values. Off is always the empty
#: string (the falsy convention the real variable plumbing uses). On-values
#: are deliberately shorter than ``_MIN_DUPLICATE_PARAGRAPH_CHARS`` so an
#: injected value repeated by design cannot fake a paragraph duplication.


def stubbed_embed_variables() -> dict[str, dict[str, str]]:
    """Return per-template variable stubs for deliberate full-document embeds.

    ``worker_developer`` embeds the complete base developer prompt via
    ``{{ base_prompt }}``: duplication between the wrapper text and the
    embedded base prompt is deliberate there, so the wrapper is audited
    with a short stub ``base_prompt`` to keep every check meaningful for
    the wrapper's own text. Any new entry must carry a one-line
    justification comment like the one above.
    """
    return {
        # worker_developer: wrapper deliberately embeds the full base developer
        # prompt; audited with a stub base_prompt per the module docstring.
        "worker_developer": {"base_prompt": "[render-integrity-audit stub base prompt]"},
    }


def deliberate_duplication_allowlist() -> frozenset[tuple[str, str]]:
    """Return ``(template, fingerprint)`` pairs exempt from duplication checks.

    The fingerprint is the first ``_DUPLICATION_FINGERPRINT_CHARS``
    characters of the normalized duplicated heading or paragraph. The
    allowlist is intentionally empty today; every entry added later must
    carry a one-line justification comment.
    """
    return frozenset()


def _template_sources(context: TemplateContext) -> tuple[dict[str, str], dict[str, str]]:
    """Return (top-level template sources, all parseable sources) by name."""
    top_level: dict[str, str] = {
        path.stem: context.registry.get_template(path.stem)
        for path in sorted(packaged_template_root().glob("*.jinja"))
    }
    all_sources: dict[str, str] = dict(context.partials)
    all_sources.update(top_level)
    return top_level, all_sources


def _referenced_variable_names(sources: dict[str, str]) -> set[str]:
    """Union of undeclared variable names across every template and partial."""
    environment = Environment()
    names: set[str] = set()
    for source in sources.values():
        names |= meta.find_undeclared_variables(environment.parse(source))
    return names - _ENGINE_GLOBAL_NAMES


def _base_variables(sources: dict[str, str]) -> dict[str, str]:
    """Build the representative base variable map for every render.

    Real capability variables (development drain defaults) take priority;
    every other referenced name gets a distinct deterministic placeholder
    so ``StrictUndefined`` never fires for reachable branches and any
    placeholder leaking verbatim stays attributable.
    """
    variables: dict[str, str] = {
        name: f"[audit-value {name}]" for name in sorted(_referenced_variable_names(sources))
    }
    capabilities, policy_flags = default_caps_and_flags_for_drain(SessionDrain.DEVELOPMENT)
    variables.update(capability_template_variables(capabilities, policy_flags))
    return variables


def _scenarios() -> tuple[tuple[str, dict[str, str]], ...]:
    """Return the named toggle combinations every template is rendered with."""
    all_off = {name: "" for name, _on in _TOGGLE_ON_VALUES}
    combos: list[tuple[str, dict[str, str]]] = [("baseline", dict(all_off))]
    for name, on_value in _TOGGLE_ON_VALUES:
        overrides = dict(all_off)
        overrides[name] = on_value
        combos.append((f"{name}=on", overrides))
    combos.append(("all-on", dict(_TOGGLE_ON_VALUES)))
    return tuple(combos)


def _duplicated_headings(lines: list[str]) -> list[tuple[str, str]]:
    """Check 3: (fingerprint, description) per heading appearing twice.

    Headings inside fenced code blocks (worked markdown examples, shell
    comments) are excluded — only real section headings of the rendered
    prompt participate.
    """
    counts: Counter[str] = Counter()
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence and _HEADING_RE.match(line):
            counts[line.strip()] += 1
    return [
        (
            heading[:_DUPLICATION_FINGERPRINT_CHARS],
            f"duplicated heading (x{count}): {heading!r}",
        )
        for heading, count in counts.items()
        if count > 1
    ]


def _duplicated_paragraphs(rendered: str) -> list[tuple[str, str]]:
    """Check 4: (fingerprint, description) per >=120-char paragraph seen twice."""
    counts: Counter[str] = Counter()
    # re.split is typed ``list[str | Any]`` (group-dependent); this pattern
    # has no groups, so every element is a plain str.
    paragraphs = cast("list[str]", re.split(r"\n\s*\n", rendered))
    for paragraph in paragraphs:
        normalized = " ".join(paragraph.split())
        if len(normalized) >= _MIN_DUPLICATE_PARAGRAPH_CHARS:
            counts[normalized] += 1
    return [
        (
            paragraph[:_DUPLICATION_FINGERPRINT_CHARS],
            f"duplicated paragraph (x{count}): "
            f"{paragraph[:_DUPLICATION_FINGERPRINT_CHARS]!r}...",
        )
        for paragraph, count in counts.items()
        if count > 1
    ]


def _blank_and_label_defects(lines: list[str]) -> list[str]:
    """Check 5: 3+ consecutive blank lines and doubled ``LABEL:`` lines."""
    defects: list[str] = []
    blank_run = 0
    longest_run = 0
    for line in lines:
        blank_run = blank_run + 1 if line.strip() == "" else 0
        longest_run = max(longest_run, blank_run)
    if longest_run > _MAX_CONSECUTIVE_BLANK_LINES:
        defects.append(f"run of {longest_run} consecutive blank lines")
    for current, following in itertools.pairwise(lines):
        stripped = current.strip()
        if stripped and stripped.endswith(":") and stripped == following.strip():
            defects.append(f"doubled label line: {stripped!r}")
    return defects


def check_rendered_prompt(template_name: str, rendered: str) -> list[str]:
    """Run integrity checks 1 and 3-5 against one rendered prompt.

    Returns human-readable violation descriptions (no template/scenario
    prefix — ``collect_violations`` adds attribution). Check 2 (include
    resolution) lives in ``collect_violations`` because it is observed as
    a rendering exception, not as a property of rendered text.
    """
    descriptions = [
        f"unrendered Jinja marker {marker!r} in output"
        for marker in _JINJA_MARKERS
        if marker in rendered
    ]
    lines = rendered.split("\n")
    allowlist = deliberate_duplication_allowlist()
    for fingerprint, description in _duplicated_headings(lines) + _duplicated_paragraphs(rendered):
        if (template_name, fingerprint) not in allowlist:
            descriptions.append(description)
    descriptions.extend(_blank_and_label_defects(lines))
    return descriptions


def _collect_findings(*, blank_lines_are_violations: bool) -> list[str]:
    """Render every template scenario and aggregate one selected finding class.

    The function is pure with respect to the repository: it reads packaged
    templates, renders them in memory, and returns attributed findings.
    """
    context = TemplateContext.default()
    top_level, all_sources = _template_sources(context)
    base = _base_variables(all_sources)
    stubs = stubbed_embed_variables()

    aggregated: dict[tuple[str, str], list[str]] = {}
    for name, source in top_level.items():
        for scenario, overrides in _scenarios():
            variables = dict(base)
            variables.update(overrides)
            variables.update(stubs.get(name, {}))
            try:
                rendered = render_template(source, variables, context.partials)
            except TemplateRenderingError as exc:
                descriptions = [f"rendering failed: {exc}"]
            else:
                descriptions = check_rendered_prompt(name, rendered)
            for description in descriptions:
                is_blank_line_warning = "consecutive blank lines" in description
                if is_blank_line_warning != blank_lines_are_violations:
                    continue
                aggregated.setdefault((name, description), []).append(scenario)

    return [
        f"{name}.jinja [{', '.join(scenarios)}]: {description}"
        for (name, description), scenarios in sorted(aggregated.items())
    ]


def collect_violations() -> list[str]:
    """Return hard render-integrity violations across every template scenario."""
    return _collect_findings(blank_lines_are_violations=False)


def collect_warnings() -> list[str]:
    """Return report-only consecutive-blank-line findings."""
    return _collect_findings(blank_lines_are_violations=True)


def main(argv: list[str] | None = None) -> int:
    """Run the render-integrity audit and return the process exit code.

    Renders every packaged top-level template across the toggle scenarios,
    prints a one-line summary on success or a labeled violation list on
    failure. Has no side effects beyond stdout output.

    Args:
        argv: Unused positional argument list (kept for CLI symmetry with
            the other audit entry points). Values are ignored.

    Returns:
        ``0`` when every template x scenario renders cleanly and passes
        all five checks, ``1`` otherwise.
    """
    del argv
    problems = collect_violations()
    warnings = collect_warnings()
    if warnings:
        print(f"TEMPLATE RENDER-INTEGRITY WARNINGS: {len(warnings)} formatting finding(s)")
        for line in warnings:
            print(f"  {line}")
    if problems:
        print(f"TEMPLATE RENDER-INTEGRITY AUDIT FAILED: {len(problems)} violation(s)")
        print("=" * 72)
        for line in problems:
            print(f"  {line}")
        print()
        print(
            "Every packaged prompt template must render without unresolved Jinja "
            "markers, missing includes, duplicated headings/paragraphs, or doubled "
            "label lines. Fix the template (or, for DELIBERATE "
            "duplication only, add a justified allowlist entry in "
            "ralph/testing/audit_template_render_integrity.py)."
        )
        return 1
    print(
        "Template render-integrity audit OK: every packaged top-level template "
        "rendered across all toggle scenarios with no unrendered markers, no "
        "include failures, no duplicated headings/paragraphs, and no doubled-label "
        "defects."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
