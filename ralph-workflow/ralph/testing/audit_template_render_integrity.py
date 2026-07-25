"""Render-integrity audit for packaged prompt templates and shared partials.

Renders every top-level template under ``ralph/prompts/templates/*.jinja`` and
every ``shared/_*`` partial through the real rendering path
(``TemplateContext.default()`` + ``TemplateRenderer``). Macro-only partials
receive small call harnesses so their bodies are validated independently
rather than only when a top-level caller happens to reach them. The audit
asserts five integrity properties on every rendered prompt:

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
5. **No blank-run or doubled-label defects** — outside fenced payloads, no
   run of 3+ consecutive blank lines; no ``LABEL:`` line immediately followed
   by an identical ``LABEL:`` line. These are hard failures, matching the
   renderer's blank-line normalization contract.

Rendered contexts use real drain capability mappings; capability and tool
variables are never toggled independently. Case-neutral condition discovery
uses Jinja's parsed undeclared-variable set, so both uppercase and lowercase
branch inputs are covered without treating keywords or local names as inputs.
Optional inputs are combined exhaustively within each reachable nested
conditional path and pairwise across a target's template closure, while
incompatible transport states such as simultaneously inlined and file-routed
payloads are excluded. Real capability profiles are crossed with those branch
paths. Every remaining undeclared variable receives a short deterministic
placeholder so ``StrictUndefined`` cannot mask an unexercised branch.
Cross-document duplication (master prompt vs template) is out of scope.

Usage:
    python -m ralph.testing.audit_template_render_integrity

Exit 0 = clean, 1 = at least one integrity violation.
"""

from __future__ import annotations

import itertools
import re
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Protocol, cast

from jinja2 import Environment, meta, nodes

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import TemplateRenderer
from ralph.prompts.template_registry import packaged_template_root
from ralph.prompts.template_rendering_error import TemplateRenderingError
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

_DUPLICATION_PREVIEW_CHARS = 60
#: Maximum duplicated paragraph text included in one diagnostic.

_JINJA_MARKERS: tuple[str, ...] = ("{{", "{%", "{#")

_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")


class _NameNodeView(Protocol):
    """Typed view of the attributes exposed by a Jinja ``Name`` node."""

    name: str
    ctx: str


class _IfNodeView(Protocol):
    """Typed view of the attributes exposed by a Jinja ``If`` node."""

    test: nodes.Node
    body: list[nodes.Node]
    elif_: list[nodes.Node]
    else_: list[nodes.Node]


def _group_sort_key(group: frozenset[str]) -> tuple[int, tuple[str, ...]]:
    """Return a deterministic sort key for one conditional-variable group."""
    return len(group), tuple(sorted(group))


_HEADING_RE = re.compile(r"^#{1,6} \S")
_TEMPLATE_REFERENCE_RE = re.compile(
    r"{%\s*(?:include|from|import)\s+['\"](?P<name>[^'\"]+)['\"]",
    re.DOTALL,
)
_ASSIGNMENT_RE = re.compile(r"{%\s*set\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")
_JINJA_COMMENT_RE = re.compile(r"{#.*?#}", re.DOTALL)

_ENGINE_GLOBAL_NAMES: frozenset[str] = frozenset({"raise_error"})
#: Names ``jinja2.meta`` reports as undeclared that are actually engine
#: globals installed by ``render_template``; they must not receive
#: placeholder values (calling a string placeholder would break rendering).

_BRANCH_VALUES: dict[str, tuple[str, ...]] = {
    "ANALYSIS_FEEDBACK": ("Analysis feedback F-1 requires a focused repair.",),
    "ANALYSIS_FEEDBACK_PATH": (".agent/tmp/analysis_feedback.md",),
    "ARTIFACT_HISTORY_PATH": (".agent/artifacts/history/index.md",),
    "HAS_DOCS_MCP": ("true",),
    "HIDE_ARTIFACT_SUBMISSION_GUIDANCE": ("true",),
    "ISSUES": ("Issue I-1 remains unresolved.",),
    "ISSUES_PATH": (".agent/tmp/issues.md",),
    "IS_CONTINUATION": ("true",),
    "IS_WORKER": ("true",),
    "LAST_RETRY_ERROR": ("Previous submission failed validation.",),
    "PRIOR_RESULT_STATUS": ("partial",),
    "SKILLS_INLINE_CONTENT": ("Use the audit-inline skill instructions.",),
    "analysis_feedback_block": ("Analysis feedback block F-1.",),
    "replaying_commit_sha": ("0123456789abcdef",),
    "shipped_skills_mode": ("planning", "development"),
    "show_plan_edit_guidance": ("true",),
}
#: Closed vocabulary for non-capability branch inputs. Adding a conditional
#: variable without declaring realistic values here fails the audit matrix.

_MUTUALLY_EXCLUSIVE_BRANCH_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"ANALYSIS_FEEDBACK", "ANALYSIS_FEEDBACK_PATH"}),
    frozenset({"ISSUES", "ISSUES_PATH"}),
)
#: Runtime routing chooses either inline content or a file path, never both.

_TOP_LEVEL_DRAINS: dict[str, SessionDrain] = {
    "commit_cleanup": SessionDrain.COMMIT,
    "commit_message": SessionDrain.COMMIT,
    "commit_simplified": SessionDrain.COMMIT,
    "conflict_resolution": SessionDrain.DEVELOPMENT,
    "developer_iteration": SessionDrain.DEVELOPMENT,
    "developer_iteration_continuation": SessionDrain.DEVELOPMENT,
    "developer_iteration_fallback": SessionDrain.DEVELOPMENT,
    "development_analysis": SessionDrain.DEVELOPMENT_ANALYSIS,
    "fix_mode": SessionDrain.FIX,
    "planning": SessionDrain.PLANNING,
    "planning_analysis": SessionDrain.ANALYSIS,
    "planning_edit": SessionDrain.PLANNING,
    "planning_edit_fallback": SessionDrain.PLANNING,
    "planning_fallback": SessionDrain.PLANNING,
    "policy_remediation": SessionDrain.DEVELOPMENT,
    "policy_remediation_analysis": SessionDrain.ANALYSIS,
    "review": SessionDrain.REVIEW,
    "review_analysis": SessionDrain.REVIEW_ANALYSIS,
    "worker_developer": SessionDrain.DEVELOPMENT,
}

_ARTIFACT_SUBMISSION_HARNESS = """\
{% from 'shared/_artifact_submission.j2' import render_artifact_submission %}
{% set audit_example -%}
---
type: skip
reason: Audit example.
---
{%- endset %}
{{ render_artifact_submission(
    'commit_message',
    SUBMIT_MD_ARTIFACT_TOOL_REFERENCE,
    audit_example,
    write_file_tool_reference=AUDIT_WRITE_FILE_TOOL_REFERENCE,
    verify_tool_reference=AUDIT_VERIFY_TOOL_REFERENCE
) }}
"""
_PAYLOAD_SECTION_HARNESS = """\
{% from 'shared/_payload_section.j2' import render_payload_section, render_payload_path %}
{{ render_payload_section('AUDIT PAYLOAD', AUDIT_PAYLOAD, AUDIT_PAYLOAD_PATH) }}
{{ render_payload_path('AUDIT REQUIRED PAYLOAD', AUDIT_REQUIRED_PAYLOAD_PATH) }}
"""
_OPTIONAL_SKILL_HARNESS = """\
{% from 'shared/_optional_artifact_skill_pointer.j2'
   import render_optional_artifact_skill_pointer %}
{{ render_optional_artifact_skill_pointer(
    'audit-skill',
    '.agent/artifact-formats/audit.md'
) }}
"""
_ANALYSIS_CONTEXT_HARNESS = """\
{% from 'shared/_payload_section.j2' import render_payload_section %}
{% include 'shared/_analysis_context.j2' %}
"""


@dataclass(frozen=True)
class _RenderTarget:
    """One independently rendered template or shared-partial harness."""

    name: str
    source: str
    drain: SessionDrain
    fixed_scenarios: tuple[tuple[str, dict[str, str]], ...] = ()


def _render_targets(context: TemplateContext) -> tuple[_RenderTarget, ...]:
    """Return every top-level template and shared partial as an audit target."""
    targets = [
        _RenderTarget(
            name=path.stem,
            source=context.registry.get_template(path.stem),
            drain=_TOP_LEVEL_DRAINS[path.stem],
        )
        for path in sorted(packaged_template_root().glob("*.jinja"))
    ]

    fixed_scenarios_by_name: dict[str, tuple[tuple[str, dict[str, str]], ...]] = {
        "shared/_artifact_submission": (
            (
                "baseline",
                {
                    "AUDIT_VERIFY_TOOL_REFERENCE": "",
                    "AUDIT_WRITE_FILE_TOOL_REFERENCE": "",
                },
            ),
            (
                "verify-tool=on",
                {
                    "AUDIT_VERIFY_TOOL_REFERENCE": "`ralph_verify_md_artifact`",
                    "AUDIT_WRITE_FILE_TOOL_REFERENCE": "",
                },
            ),
            (
                "write-fallback=on",
                {
                    "AUDIT_VERIFY_TOOL_REFERENCE": "",
                    "AUDIT_WRITE_FILE_TOOL_REFERENCE": "`write_file`",
                },
            ),
        ),
        "shared/_payload_section": (
            (
                "inline",
                {
                    "AUDIT_PAYLOAD": "Audit payload body.",
                    "AUDIT_PAYLOAD_PATH": "",
                    "AUDIT_REQUIRED_PAYLOAD_PATH": ".agent/tmp/required.md",
                },
            ),
            (
                "file-reference",
                {
                    "AUDIT_PAYLOAD": "",
                    "AUDIT_PAYLOAD_PATH": ".agent/tmp/audit-payload.md",
                    "AUDIT_REQUIRED_PAYLOAD_PATH": ".agent/tmp/required.md",
                },
            ),
        ),
    }
    harnesses = {
        "shared/_analysis_context": _ANALYSIS_CONTEXT_HARNESS,
        "shared/_artifact_submission": _ARTIFACT_SUBMISSION_HARNESS,
        "shared/_optional_artifact_skill_pointer": _OPTIONAL_SKILL_HARNESS,
        "shared/_payload_section": _PAYLOAD_SECTION_HARNESS,
    }
    for name in sorted(key for key in context.partials if key.startswith("shared/")):
        source = harnesses.get(name, f"{{% include '{name}.j2' %}}")
        targets.append(
            _RenderTarget(
                name=name,
                source=source,
                drain=(
                    SessionDrain.PLANNING
                    if name in {"shared/_mcp_tools", "shared/_planning_subagents"}
                    else SessionDrain.DEVELOPMENT
                ),
                fixed_scenarios=fixed_scenarios_by_name.get(name, ()),
            )
        )
    return tuple(targets)


def _referenced_variable_names(sources: dict[str, str]) -> set[str]:
    """Union of undeclared variable names across every template and partial."""
    environment = Environment()
    names: set[str] = set()
    for source in sources.values():
        names |= meta.find_undeclared_variables(environment.parse(source))
    return names - _ENGINE_GLOBAL_NAMES


def _base_variables(sources: dict[str, str]) -> dict[str, str]:
    """Build deterministic placeholders for every non-engine variable."""
    return {name: f"[audit-value {name}]" for name in sorted(_referenced_variable_names(sources))}


def _loaded_names(node: nodes.Node) -> set[str]:
    """Return load-context variable names referenced by one Jinja AST node."""
    names: set[str] = set()
    if type(node).__name__ == "Name":
        name_node = cast(
            "_NameNodeView", node
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        if name_node.ctx == "load":
            names.add(name_node.name)
    for child in node.iter_child_nodes():
        names.update(_loaded_names(child))
    return names


def _conditional_variable_groups(
    sources: dict[str, str],
) -> tuple[frozenset[str], ...]:
    """Return variable groups required along every nested conditional path."""
    environment = Environment()
    groups: set[frozenset[str]] = set()
    for source in sources.values():
        parsed = environment.parse(source)
        undeclared = meta.find_undeclared_variables(parsed)

        def walk(
            node: nodes.Node,
            ancestors: frozenset[str],
            undeclared_names: set[str],
        ) -> None:
            if type(node).__name__ == "If":
                if_node = cast(
                    "_IfNodeView", node
                )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
                condition_names = frozenset(_loaded_names(if_node.test) & undeclared_names)
                path_names = (ancestors | condition_names) - _ENGINE_GLOBAL_NAMES
                if path_names:
                    groups.add(path_names)
                for child in if_node.body:
                    walk(child, path_names, undeclared_names)
                for child in if_node.elif_:
                    walk(child, path_names, undeclared_names)
                for child in if_node.else_:
                    walk(child, path_names, undeclared_names)
                return
            for child in node.iter_child_nodes():
                walk(child, ancestors, undeclared_names)

        walk(parsed, frozenset(), undeclared)
    return tuple(sorted(groups, key=_group_sort_key))


def _conditional_variable_names(sources: dict[str, str]) -> set[str]:
    """Return undeclared uppercase and lowercase names used by Jinja conditions."""
    groups = _conditional_variable_groups(sources)
    return set().union(*groups) if groups else set()


def _normalized_partial_name(name: str) -> str:
    for suffix in (".jinja", ".j2", ".txt"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _referenced_partial_names(source: str) -> set[str]:
    """Return constant include/import names from one template source."""
    uncommented = _JINJA_COMMENT_RE.sub("", source)
    return {
        _normalized_partial_name(match.group("name"))
        for match in _TEMPLATE_REFERENCE_RE.finditer(uncommented)
    }


def _template_source_closure(source: str, partials: dict[str, str]) -> dict[str, str]:
    """Return one template plus every partial it directly or transitively references."""
    closure = {"__main__": source}
    pending = [source]
    while pending:
        current = pending.pop()
        for name in _referenced_partial_names(current):
            if name in closure:
                continue
            partial = partials.get(name)
            if partial is None:
                continue
            closure[name] = partial
            pending.append(partial)
    return closure


def _assigned_variable_names(sources: dict[str, str]) -> set[str]:
    """Return names assigned by ``set`` statements in a template closure."""
    assigned: set[str] = set()
    for source in sources.values():
        uncommented = _JINJA_COMMENT_RE.sub("", source)
        assigned.update(match.group("name") for match in _ASSIGNMENT_RE.finditer(uncommented))
    return assigned


def _build_capability_profiles() -> tuple[
    tuple[SessionDrain, tuple[tuple[str, str], ...]],
    ...,
]:
    """Return immutable real capability-variable profiles for every drain."""
    profiles: list[tuple[SessionDrain, tuple[tuple[str, str], ...]]] = []
    for drain in SessionDrain:
        capabilities, policy_flags = default_caps_and_flags_for_drain(drain)
        variables = capability_template_variables(capabilities, policy_flags)
        profiles.append((drain, tuple(sorted(variables.items()))))
    return tuple(profiles)


_CAPABILITY_PROFILES = _build_capability_profiles()
_CAPABILITY_VARIABLE_NAMES = frozenset(
    name for _drain, profile in _CAPABILITY_PROFILES for name, _value in profile
)


def _branch_scenarios(
    condition_names: set[str],
    *,
    condition_groups: tuple[frozenset[str], ...] | None = None,
) -> tuple[tuple[str, dict[str, str]], ...]:
    """Return exhaustive compatible values for every conditional path."""
    optional_names = condition_names - _CAPABILITY_VARIABLE_NAMES
    unknown = optional_names - _BRANCH_VALUES.keys()
    if unknown:
        missing_names = ", ".join(sorted(unknown))
        raise ValueError(f"no realistic render scenario values declared for: {missing_names}")

    all_off = dict.fromkeys(sorted(optional_names), "")
    scenarios: list[tuple[str, dict[str, str]]] = [("baseline", all_off)]
    seen = {tuple(sorted(all_off.items()))}
    path_groups = {
        frozenset(group & optional_names)
        for group in (condition_groups or ())
        if group & optional_names
    }
    path_groups.update(frozenset({name}) for name in optional_names)
    groups = set(path_groups)
    groups.update(left | right for left, right in itertools.combinations(path_groups, 2))
    for group in sorted(groups, key=_group_sort_key):
        scenario_names = sorted(group)
        choices = [("", *_BRANCH_VALUES[name]) for name in scenario_names]
        for values in itertools.product(*choices):
            if not any(values):
                continue
            overrides = dict(all_off)
            overrides.update(zip(scenario_names, values, strict=True))
            if any(
                sum(bool(overrides.get(name, "")) for name in exclusive_group) > 1
                for exclusive_group in _MUTUALLY_EXCLUSIVE_BRANCH_GROUPS
            ):
                continue
            signature = tuple(sorted(overrides.items()))
            if signature in seen:
                continue
            seen.add(signature)
            labels = [
                (f"{name}={value}" if len(_BRANCH_VALUES[name]) > 1 else f"{name}=on")
                for name, value in zip(scenario_names, values, strict=True)
                if value
            ]
            scenarios.append(("+".join(labels), overrides))
    return tuple(scenarios)


def _profile_scenarios(
    target: _RenderTarget,
    condition_names: set[str],
) -> tuple[tuple[str, dict[str, str]], ...]:
    """Return real capability profiles needed by one target's conditions."""
    profiles = _CAPABILITY_PROFILES
    by_drain = {drain: dict(profile) for drain, profile in profiles}
    baseline = by_drain[target.drain]
    scenarios: list[tuple[str, dict[str, str]]] = [(target.drain.value, baseline)]
    if not target.name.startswith("shared/"):
        return tuple(scenarios)

    relevant_names = sorted(condition_names & _CAPABILITY_VARIABLE_NAMES)
    baseline_signature = tuple((name, baseline[name]) for name in relevant_names)
    seen = {baseline_signature}
    for drain, frozen_profile in profiles:
        profile = dict(frozen_profile)
        signature = tuple((name, profile[name]) for name in relevant_names)
        if signature in seen:
            continue
        seen.add(signature)
        scenarios.append((drain.value, profile))
    return tuple(scenarios)


def _target_scenarios(
    target: _RenderTarget,
    condition_names: set[str],
    condition_groups: tuple[frozenset[str], ...],
) -> tuple[tuple[str, dict[str, str]], ...]:
    """Cross real capabilities with every reachable branch and harness case."""
    branches = _branch_scenarios(
        condition_names,
        condition_groups=condition_groups,
    )
    profiles = _profile_scenarios(target, condition_names)
    fixed = target.fixed_scenarios or (("baseline", {}),)

    cases: list[tuple[str, dict[str, str]]] = []
    for profile_index, (profile_name, profile) in enumerate(profiles):
        for branch_index, (branch_name, branch) in enumerate(branches):
            for fixed_index, (fixed_name, fixed_values) in enumerate(fixed):
                variables = dict(profile)
                variables.update(branch)
                variables.update(fixed_values)
                labels: list[str] = []
                if profile_index:
                    labels.append(f"capabilities={profile_name}")
                if branch_index:
                    labels.append(branch_name)
                if fixed_index:
                    labels.append(fixed_name)
                cases.append(("+".join(labels) or "baseline", variables))
    return tuple(cases)


def _duplicated_headings(lines: list[str]) -> list[str]:
    """Check 3: one description per heading appearing twice.

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
        f"duplicated heading (x{count}): {heading!r}"
        for heading, count in counts.items()
        if count > 1
    ]


def _duplicated_paragraphs(rendered: str) -> list[str]:
    """Check 4: one description per >=120-char paragraph seen twice."""
    counts: Counter[str] = Counter()
    # re.split is typed ``list[str | Any]`` (group-dependent); this pattern
    # has no groups, so every element is a plain str.
    paragraphs = cast(
        "list[str]", re.split(r"\n\s*\n", rendered)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    for paragraph in paragraphs:
        normalized = " ".join(paragraph.split())
        if len(normalized) >= _MIN_DUPLICATE_PARAGRAPH_CHARS:
            counts[normalized] += 1
    return [
        f"duplicated paragraph (x{count}): {paragraph[:_DUPLICATION_PREVIEW_CHARS]!r}..."
        for paragraph, count in counts.items()
        if count > 1
    ]


def _blank_and_label_defects(lines: list[str]) -> list[str]:
    """Check 5 outside fences: 3+ blank lines and doubled ``LABEL:`` lines."""
    defects: list[str] = []
    first_content = 0
    last_content = len(lines)
    while first_content < last_content and not lines[first_content].strip():
        first_content += 1
    while last_content > first_content and not lines[last_content - 1].strip():
        last_content -= 1
    bounded_lines = lines[first_content:last_content]

    blank_run = 0
    longest_run = 0
    in_fence = False
    for line in bounded_lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            blank_run = 0
            continue
        if in_fence:
            continue
        blank_run = blank_run + 1 if line.strip() == "" else 0
        longest_run = max(longest_run, blank_run)
    if longest_run > _MAX_CONSECUTIVE_BLANK_LINES:
        defects.append(f"run of {longest_run} consecutive blank lines")

    unfenced_lines: list[str] = []
    in_fence = False
    for line in bounded_lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            unfenced_lines.append(line)
    for current, following in itertools.pairwise(unfenced_lines):
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
    descriptions.extend(_duplicated_headings(lines))
    descriptions.extend(_duplicated_paragraphs(rendered))
    descriptions.extend(_blank_and_label_defects(lines))
    return descriptions


def _collect_findings() -> list[str]:
    """Render every top-level/shared target scenario and aggregate every finding.

    The function is pure with respect to the repository: it reads packaged
    templates, renders them in memory, and returns attributed findings.
    """
    context = TemplateContext.default()
    targets = _render_targets(context)
    all_sources = dict(context.partials)
    all_sources.update({target.name: target.source for target in targets})
    base = _base_variables(all_sources)
    renderer = TemplateRenderer(context.partials)
    partials = dict(context.partials)

    aggregated: dict[tuple[str, str], list[str]] = {}
    for target in targets:
        closure = _template_source_closure(target.source, partials)
        assigned_names = _assigned_variable_names(closure)
        condition_groups = tuple(
            frozenset(group - assigned_names)
            for group in _conditional_variable_groups(closure)
            if group - assigned_names
        )
        condition_names = set().union(*condition_groups) if condition_groups else set()
        try:
            scenarios = _target_scenarios(
                target,
                condition_names,
                condition_groups,
            )
        except ValueError as exc:
            aggregated.setdefault(
                (target.name, f"scenario matrix invalid: {exc}"),
                [],
            ).append("configuration")
            continue

        for scenario, overrides in scenarios:
            variables = dict(base)
            variables.update(overrides)
            try:
                rendered = renderer.render(target.source, variables)
            except TemplateRenderingError as exc:
                descriptions = [f"rendering failed: {exc}"]
            else:
                descriptions = check_rendered_prompt(target.name, rendered)
            for description in descriptions:
                aggregated.setdefault((target.name, description), []).append(scenario)

    return [
        f"{name} [{', '.join(scenarios)}]: {description}"
        for (name, description), scenarios in sorted(aggregated.items())
    ]


def collect_violations() -> list[str]:
    """Return hard violations across every template/partial render scenario."""
    return _collect_findings()


def main(argv: list[str] | None = None) -> int:
    """Run the render-integrity audit and return the process exit code.

    Renders every packaged top-level template and shared partial across real
    capability and optional-input scenarios, then prints a one-line summary
    or a labeled violation list. Has no side effects beyond stdout output.

    Args:
        argv: Unused positional argument list (kept for CLI symmetry with
            the other audit entry points). Values are ignored.

    Returns:
        ``0`` when every template x scenario renders cleanly and passes
        all five checks, ``1`` otherwise.
    """
    del argv
    problems = collect_violations()
    if problems:
        print(f"TEMPLATE RENDER-INTEGRITY AUDIT FAILED: {len(problems)} violation(s)")
        print("=" * 72)
        for line in problems:
            print(f"  {line}")
        print()
        print(
            "Every packaged prompt template must render without unresolved Jinja "
            "markers, missing includes, duplicated headings/paragraphs, or doubled "
            "label/blank-run defects. Fix the template or its realistic scenario "
            "mapping."
        )
        return 1
    print(
        "Template render-integrity audit OK: every packaged top-level template "
        "and shared partial rendered across reachable scenarios with no unrendered "
        "markers, include failures, duplicated headings/paragraphs, or "
        "label/blank-run defects."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
