"""Rendered parallel-execution guidance asserts role-aware dispatch contracts.

The shared partials must produce role-aware prose on the public rendering
surface (``prompt_developer_iteration_xml_with_context`` with a
``MemoryWorkspace``). Source-text inspection is intentionally avoided:
rendered-output assertions catch template regressions that source grep
misses, and they survive partial rewording.

The contract has two halves:
* Coordinator renderings (regular, continuation, fallback) must surface
  the parallel-by-default anchors AND keep the existing four shared
  contract behaviors (independent ready steps, queue/local progress,
  exposed-tool discipline, stopped-writer transfer).
* Worker renderings (``worker_developer.jinja``, continuation+worker,
  fallback+worker) must keep their assignment-local framing and must
  NOT receive the coordinator-only orchestration anchors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.protocol.capability_mapping import Capability
from ralph.prompts._capability_set import CapabilitySet
from ralph.prompts._policy_flag import PolicyFlag
from ralph.prompts._policy_flag_set import PolicyFlagSet
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

# Coordinator-only behaviors the S-1/U-1 work must make visible on every
# coordinator rendering. These are the four pre-existing contract anchors
# (independent ready steps, queue/local progress, exposed-tool discipline,
# stopped-writer transfer) plus the parallel-by-default mandatory wording.
_COORDINATOR_INDEPENDENT_READY = "independent ready"
_COORDINATOR_QUEUE_OR_LOCAL = "queue"
_COORDINATOR_EXPOSED_TOOLS = "exposed"
_COORDINATOR_STOPPED_WRITER = "stopped"

# Coordinator-only pinned anchors (exact literals). These must surface on
# every coordinator rendering and must be ABSENT from every worker
# rendering. The shared wording contract is the single source of truth
# for the parallel-by-default rewrite.
_COORDINATOR_PINS_REQUIRED: tuple[str, ...] = (
    "Parallel execution of independent ready units is required by default",
    "dispatch every ready unit concurrently",
    "Sequential execution requires an explicit plan reason",
    "not a reason to stop, hand back, split the task, or return `partial`",
    "which plan text forced it",
    "## PARALLEL EXECUTION (required by default)",
)

# Negative rendered-output anchors: no coordinator rendering may emit any
# of the removed fallback phrases, regardless of capability input. The
# source-level grep check in S-1 alone is insufficient because include /
# render paths could reintroduce text from elsewhere; the rendered-output
# assertion closes that gap.
_COORDINATOR_RENDERED_NEGATIVES: tuple[str, ...] = (
    "missing sub-agent tool",
    "bounded sequential",
    "bounded-sequential",
    "fall back to bounded sequential",
    "no native sub-agent",
    "runtime limit",
)

# Negative source guards: the shared partial must not contain the
# removed fallback phrases. Source-level guards protect future partial
# rewrites from re-introducing capability-gated sequential fallbacks.
_PARTIAL_SOURCE_NEGATIVES: tuple[str, ...] = (
    "or a missing sub-agent tool",
    "bounded-sequential",
    "no native sub-agent / task tool is exposed",
    "runtime limit",
)

# Worker-only prohibitions: the continuation and first-iteration templates
# must NOT leak coordinator dispatch or coordinator pre-submit review
# mandates to worker renderings.
_WORKER_DISPATCH_PROHIBITION = "dispatch independent ready groups"
_WORKER_REVIEW_PROHIBITION = "independent read-only sub-agent"
_WORKER_TRANSFER_PROHIBITION = "stopped-writer transfer"

# Worker-only retention: workers must keep an assignment-local recovery
# framing so the partial-progress-escape removal does not strip their
# "within your assignment" guard. The exact phrasing is intentionally
# flexible — a strict verbatim contract would over-fit a stylistic
# choice; the contract is "workers stay assignment-scoped", which can
# be expressed several ways without weakening the guard.
_WORKER_ASSIGNMENT_LOCAL_CLAUSES = (
    "within your assignment",
    "the assigned unit",
    "your scope",
    "the assigned work unit",
)

# Distinct ownership/waves protections: the shared parallel-execution
# partial must keep the exact-path ownership, waves, and protected-path
# sanitization language on the coordinator rendering.
_PROTECTED_ROOTS = (".agent", ".git", ".worktrees")
_OWNERSHIP_FIELDS = ("Paths:", "Directories:", "Files:")


def _render(template_name: str, tmp_path: Path, *, is_worker: bool) -> str:
    """Render one development prompt surface to a whitespace-normalized form."""
    return " ".join(
        prompt_developer_iteration_xml_with_context(
            context=TemplateContext.default(),
            inputs=DeveloperPromptInputs(
                prompt_content="Implement the requested change.",
                plan_content="### [S-1] Implement the assigned change",
                work_unit_id="unit" if is_worker else "",
                work_unit_description="Implement the assigned change" if is_worker else "",
                work_unit_directories="src" if is_worker else "",
            ),
            workspace=MemoryWorkspace(root=str(tmp_path)),
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            template_name=template_name,
        ).split()
    )


def _render_with_caps(
    template_name: str,
    tmp_path: Path,
    *,
    is_worker: bool,
    session_caps: SessionCapabilities,
) -> str:
    """Render one development prompt surface with caller-supplied capability inputs.

    Returns the whitespace-normalized form used by the role-aware
    contracts in the rest of this module.
    """
    return " ".join(
        prompt_developer_iteration_xml_with_context(
            context=TemplateContext.default(),
            inputs=DeveloperPromptInputs(
                prompt_content="Implement the requested change.",
                plan_content="### [S-1] Implement the assigned change",
                work_unit_id="unit" if is_worker else "",
                work_unit_description="Implement the assigned change" if is_worker else "",
                work_unit_directories="src" if is_worker else "",
            ),
            workspace=MemoryWorkspace(root=str(tmp_path)),
            session_caps=session_caps,
            template_name=template_name,
        ).split()
    )


def _render_raw_with_caps(
    template_name: str,
    tmp_path: Path,
    *,
    is_worker: bool,
    session_caps: SessionCapabilities,
) -> str:
    """Render one development prompt surface verbatim, with newlines preserved.

    The capability-input invariance test needs to extract a section that
    ends at a ``\\n## `` boundary; whitespace normalization would erase
    that boundary, so this variant returns the raw rendered prompt.
    """
    return prompt_developer_iteration_xml_with_context(
        context=TemplateContext.default(),
        inputs=DeveloperPromptInputs(
            prompt_content="Implement the requested change.",
            plan_content="### [S-1] Implement the assigned change",
            work_unit_id="unit" if is_worker else "",
            work_unit_description="Implement the assigned change" if is_worker else "",
            work_unit_directories="src" if is_worker else "",
        ),
        workspace=MemoryWorkspace(root=str(tmp_path)),
        session_caps=session_caps,
        template_name=template_name,
    )


def _extract_parallel_section(rendered: str) -> str:
    """Return the parallel-by-default partial content from a full render.

    The shared partial is the sole author of the parallel-by-default
    contract. The fallback template places the partial between other
    tool-name-bearing prose, so the next-``## ``-heading cut includes
    text outside the partial; instead, return the prose between the
    parallel partial's first stable opening and its last stable closing
    sentence. The closing anchor is the final sentence of
    ``shared/_parallel_execution.jinja``; if it is not present the
    rendered prompt does not include the partial at all and the test
    should fail loudly elsewhere.
    """
    start = rendered.find("Parallel execution of independent ready units is required by default")
    if start < 0:
        return ""
    closing = "even when extraction cannot represent them as worker assignments."
    end = rendered.find(closing, start)
    if end < 0:
        return rendered[start:]
    return rendered[start : end + len(closing)]


@pytest.mark.parametrize(
    ("template_name", "is_worker"),
    (
        ("developer_iteration.jinja", False),
        ("developer_iteration_continuation.jinja", False),
        ("developer_iteration_fallback.jinja", False),
        ("worker_developer.jinja", True),
        ("developer_iteration_continuation.jinja", True),
        ("developer_iteration_fallback.jinja", True),
    ),
)
def test_rendered_parallel_execution_contracts_match_role(
    tmp_path: Path, template_name: str, *, is_worker: bool
) -> None:
    """S-1/S-2: every surface renders role-aware parallel-execution prose.

    Coordinator renderings must surface the four shared-contract behaviors
    (independent ready steps, queue/local progress, exposed-tool discipline,
    stopped-writer transfer) AND every coordinator-only pinned anchor from
    the parallel-by-default rewrite. Worker renderings must retain
    assignment-local recovery framing and must NOT receive any
    coordinator-only pinned anchor (orchestration language must never reach
    a worker), in addition to the existing dispatch / review /
    stopped-writer-transfer prohibitions.
    """
    rendered = _render(template_name, tmp_path, is_worker=is_worker)
    rendered_lower = rendered.lower()

    if is_worker:
        # Workers never dispatch or coordinate other units; the shared
        # worker verification partial already enforces this, and the
        # continuation/fallback templates must keep the role guard.
        assert _WORKER_DISPATCH_PROHIBITION not in rendered
        assert _WORKER_TRANSFER_PROHIBITION not in rendered
        assert _WORKER_REVIEW_PROHIBITION not in rendered
        # Workers retain assignment-local recovery framing.
        assert any(clause in rendered for clause in _WORKER_ASSIGNMENT_LOCAL_CLAUSES), (
            f"worker lost assignment-local recovery framing in {template_name}"
        )
        # Coordinator-only pinned anchors must NEVER reach a worker. If
        # any of these leak into a worker rendering, the worker would
        # start orchestrating siblings, which is a contract violation.
        leaked = [pin for pin in _COORDINATOR_PINS_REQUIRED if pin in rendered]
        assert not leaked, (
            f"worker rendering {template_name!r} leaked coordinator-only "
            f"orchestration anchors: {leaked!r}"
        )
    else:
        # Coordinator surfaces must keep the four contract anchors. The
        # exact phrasing is intentionally avoided — the shared partial
        # may reword — but the contract anchors are non-negotiable.
        assert _COORDINATOR_INDEPENDENT_READY in rendered_lower
        assert _COORDINATOR_QUEUE_OR_LOCAL in rendered_lower
        assert _COORDINATOR_EXPOSED_TOOLS in rendered_lower
        assert _COORDINATOR_STOPPED_WRITER in rendered_lower
        # Distinct ownership/waves protections stay visible.
        for token in _PROTECTED_ROOTS:
            assert token in rendered, f"protected root {token!r} missing in {template_name}"
        for token in _OWNERSHIP_FIELDS:
            assert token in rendered, f"ownership token {token!r} missing in {template_name}"
        # Parallel-by-default pinned anchors must surface on every
        # coordinator rendering. The contract is the single source of
        # truth for the rewrite.
        missing = [pin for pin in _COORDINATOR_PINS_REQUIRED if pin not in rendered]
        assert not missing, (
            f"coordinator rendering {template_name!r} missing pinned "
            f"parallel-by-default anchors: {missing!r}"
        )


def test_shared_parallel_partial_keeps_sanitization_and_waves() -> None:
    """The shared partial keeps protected-path sanitization and waves.

    Source-text guard for the partial itself: the partial is the only
    author of those protections, and a future partial rewrite must keep
    the protected-path and ownership tokens verbatim.
    """
    partial = (
        Path(__file__).resolve().parents[1]
        / "ralph"
        / "prompts"
        / "templates"
        / "shared"
        / "_parallel_execution.jinja"
    )
    source = partial.read_text(encoding="utf-8")

    for token in _PROTECTED_ROOTS:
        assert token in source, f"protected root {token!r} missing in parallel partial"
    for token in _OWNERSHIP_FIELDS:
        assert token in source, f"ownership token {token!r} missing in parallel partial"
    assert "waves" in source
    assert "Serialize conflicting ownership" in source
    assert "Do not broaden file ownership" in source
    # Parallel-by-default rewrite: the partial must open with the new
    # mandatory wording, must state the explicit plan reason, and must
    # keep the large-plan-cue phrasing.
    assert "Parallel execution of independent ready units is required by default" in source
    assert "Sequential execution requires an explicit plan reason" in source
    assert "not a reason to stop, hand back, split the task, or return `partial`" in source
    # Queue-in-waves split: a full cap queues remaining ready units, the
    # ownership-unresolvable and malformed-graph safeguards survive.
    assert "queue the remaining ready units" in source
    assert "ownership unresolvable" in source
    assert "malformed" in source
    # Negative source guards: the removed fallback phrases must not
    # survive in the partial. These are the S-1 step 5 / S-2 step 1
    # negative pins.
    for forbidden in _PARTIAL_SOURCE_NEGATIVES:
        assert forbidden not in source, (
            f"parallel partial re-introduced removed fallback phrase {forbidden!r}"
        )


# Coordinator templates that must satisfy the capability-input
# invariance and negative rendered fallback assertions. Worker-only
# templates intentionally do not appear here — the contract is
# "no non-sub-agent fallback wording reaches a worker", which the
# role-aware test already covers.
_COORDINATOR_TEMPLATES: tuple[str, ...] = (
    "developer_iteration.jinja",
    "developer_iteration_continuation.jinja",
    "developer_iteration_fallback.jinja",
)


def _maximally_different_caps() -> SessionCapabilities:
    """Return a SessionCapabilities deliberately opposed to the defaults.

    Drops the default shell + write capabilities, adds a non-default
    tool-name prefix, and flips one policy flag. The point is not the
    specific values but that the parallel-execution section is
    byte-identical under maximally different inputs: no capability
    input reaches the parallel wording.
    """
    return SessionCapabilities(
        capabilities=CapabilitySet(
            (
                Capability.WORKSPACE_READ,
                Capability.ARTIFACT_SUBMIT,
                Capability.ARTIFACT_PLAN_READ,
                Capability.RUN_REPORT_PROGRESS,
                Capability.GIT_STATUS_READ,
            )
        ),
        policy_flags=PolicyFlagSet((PolicyFlag.ALLOW_NETWORK, PolicyFlag.ALLOW_ENV_READ)),
        tool_name_prefix="alternate_runtime_",
    )


@pytest.mark.parametrize("template_name", _COORDINATOR_TEMPLATES)
def test_coordinator_parallel_section_is_capability_invariant(
    tmp_path: Path, template_name: str
) -> None:
    """The parallel-execution section must not depend on capability inputs.

    Renders the same coordinator template under
    ``SessionCapabilities.defaults_for_drain(DEVELOPMENT)`` and a
    maximally different ``SessionCapabilities``, then extracts the
    ``## PARALLEL EXECUTION`` section from each full render and asserts
    the two are byte-identical. The same section must also contain the
    parallel-by-default mandatory wording and must NOT contain any of
    the removed fallback phrases under either capability input.

    A partial-only render of ``shared/_parallel_execution.jinja`` is
    not used: capability-sensitive variables can affect coordinator
    includes and framing outside the partial, and only a full-prompt
    section comparison proves no capability input reaches the parallel
    wording.
    """
    default_caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    alternate_caps = _maximally_different_caps()

    default_rendered = _render_raw_with_caps(
        template_name, tmp_path, is_worker=False, session_caps=default_caps
    )
    alternate_rendered = _render_raw_with_caps(
        template_name, tmp_path, is_worker=False, session_caps=alternate_caps
    )

    default_section = _extract_parallel_section(default_rendered)
    alternate_section = _extract_parallel_section(alternate_rendered)

    assert default_section, f"parallel section missing from {template_name!r} default render"
    assert alternate_section, f"parallel section missing from {template_name!r} alternate render"
    assert default_section == alternate_section, (
        f"parallel section in {template_name!r} changed under alternate "
        f"capability inputs; capability gating is forbidden"
    )

    # Both renders must contain the parallel-by-default anchors and must
    # NOT contain the removed fallback phrases. This is the negative
    # rendered fallback assertion that closes the include / render gap
    # the source-level grep cannot cover. The heading literal is
    # verified against the full render, not the section, because the
    # section starts inside the heading for templates that anchor the
    # partial after the heading text.
    for pin in _COORDINATOR_PINS_REQUIRED:
        assert pin in default_rendered, (
            f"full render missing anchor {pin!r} under default caps in {template_name!r}"
        )
        assert pin in alternate_rendered, (
            f"full render missing anchor {pin!r} under alternate caps in {template_name!r}"
        )
    for forbidden in _COORDINATOR_RENDERED_NEGATIVES:
        assert forbidden not in default_section, (
            f"parallel section re-introduced {forbidden!r} under default caps in {template_name!r}"
        )
        assert forbidden not in alternate_section, (
            f"parallel section re-introduced {forbidden!r} under alternate caps in {template_name!r}"
        )


@pytest.mark.parametrize("template_name", _COORDINATOR_TEMPLATES)
def test_coordinator_full_render_excludes_removed_fallback_phrases(
    tmp_path: Path, template_name: str
) -> None:
    """The full coordinator render must not contain any removed fallback.

    A direct, in-render negative guard: the same forbidden phrases the
    partial source must not contain must also not appear anywhere in a
    full coordinator render, under either capability input. Catches
    regressions where a future include or template framing reintroduces
    the bounded-sequential escape hatch outside the parallel partial.
    """
    for caps in (
        SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        _maximally_different_caps(),
    ):
        rendered = _render_raw_with_caps(
            template_name, tmp_path, is_worker=False, session_caps=caps
        )
        for forbidden in _COORDINATOR_RENDERED_NEGATIVES:
            assert forbidden not in rendered, (
                f"full {template_name!r} render contains forbidden phrase "
                f"{forbidden!r} under caps {caps!r}"
            )
