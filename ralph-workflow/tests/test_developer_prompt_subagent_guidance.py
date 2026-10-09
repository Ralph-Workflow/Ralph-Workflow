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
  exposed-tool sequential fallback, stopped-writer transfer).
* Worker renderings (``worker_developer.jinja``, continuation+worker,
  fallback+worker) must keep their assignment-local framing and must
  NOT receive the coordinator-only orchestration anchors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

# Coordinator-only behaviors the S-1/U-1 work must make visible on every
# coordinator rendering. These are the four pre-existing contract anchors
# (independent ready steps, queue/local progress, exposed-tool sequential
# fallback, stopped-writer transfer) plus the parallel-by-default
# mandatory wording.
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
    "Sequential execution requires an explicit plan reason or a missing sub-agent tool",
    "not a reason to stop, hand back, split the task, or return `partial`",
    "which plan text or runtime limit forced it",
    "## PARALLEL EXECUTION (required by default)",
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
    (independent ready steps, queue/local progress, exposed-tool sequential
    fallback, stopped-writer transfer) AND every coordinator-only pinned
    anchor from the parallel-by-default rewrite. Worker renderings must
    retain assignment-local recovery framing and must NOT receive any
    coordinator-only pinned anchor (orchestration language must never
    reach a worker), in addition to the existing dispatch / review /
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
    # Rephrased bounded-sequential fallback sentence preserves behavior
    # but drops the contradictory "When the plan declares units the
    # runtime cannot dispatch" framing.
    assert "When the runtime cannot dispatch declared units" in source
