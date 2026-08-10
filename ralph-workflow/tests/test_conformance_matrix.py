"""Tests for the F4 cross-transport smoke-gate conformance matrix.

The matrix turns repeated single-transport ``ralph smoke-interactive-*``
runs into the shippable artefact the Evidence Provenance brief asks for
(F4): a durable table naming which runtimes reach Ralph's tools natively,
which need a dispatcher, and which can only ever run degraded -- on which
specific contract fact. Exercised entirely through an in-memory
``FileBackend`` (see ``tests/_artifact_format_docs_memory_backend.py`` for
the established pattern this file follows) -- no real filesystem I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.builtin import builtin_supports
from ralph.agents.display_capabilities import (
    DisplayCapability,
    all_display_capabilities,
)
from ralph.agents.display_capability_stance import DisplayCapabilityStance
from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.pipeline.plumbing.smoke_evidence import Evidence, Provenance
from ralph.pipeline.plumbing.smoke_plumbing import (
    CANONICAL_CAPABILITY_AGENT_ORDER,
    CONFORMANCE_MATRIX_FACTS,
    CONFORMANCE_MATRIX_TRANSPORT_ORDER,
    conformance_matrix_paths,
    load_conformance_matrix,
    record_conformance_matrix,
    render_capability_matrix_markdown,
    render_conformance_matrix_markdown,
    update_conformance_matrix,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _MemoryBackend(FileBackend):
    """Minimal in-memory FileBackend double -- no real filesystem I/O."""

    def __init__(self) -> None:
        self._files: dict[Path, str] = {}

    def exists(self, path: Path) -> bool:
        return path in self._files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del path, parents, exist_ok

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self._files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self._files[path] = content

    def read_bytes(self, path: Path) -> bytes:
        return self._files[path].encode("utf-8")

    def write_bytes(self, path: Path, content: bytes) -> None:
        self._files[path] = content.decode("utf-8")

    def replace(self, source: Path, destination: Path) -> None:
        self._files[destination] = self._files.pop(source)

    def sync_directory(self, path: Path) -> None:
        del path

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


def _wire(detail: str) -> Evidence:
    return Evidence(holds=True, provenance=Provenance.WIRE, detail=detail)


def _degraded(provenance: Provenance, detail: str) -> Evidence:
    return Evidence(holds=provenance is not Provenance.ABSENT, provenance=provenance, detail=detail)


def _full_evidence(provenance: Provenance) -> dict[str, Evidence]:
    return {fact: _degraded(provenance, f"{fact} at {provenance.name}") for fact in CONFORMANCE_MATRIX_FACTS}


# ---------------------------------------------------------------------------
# update_conformance_matrix: pure row replacement
# ---------------------------------------------------------------------------


def test_update_conformance_matrix_adds_a_new_transport_row() -> None:
    matrix = update_conformance_matrix({}, transport="agy", evidence=_full_evidence(Provenance.TRANSCRIPT))
    assert set(matrix) == {"agy"}
    assert set(matrix["agy"]) == set(CONFORMANCE_MATRIX_FACTS)


def test_update_conformance_matrix_is_pure_and_does_not_mutate_input() -> None:
    original: dict[str, dict[str, Evidence]] = {}
    update_conformance_matrix(original, transport="agy", evidence=_full_evidence(Provenance.WIRE))
    assert original == {}


def test_update_conformance_matrix_replaces_only_the_named_transport_row() -> None:
    matrix = update_conformance_matrix({}, transport="agy", evidence=_full_evidence(Provenance.TRANSCRIPT))
    matrix = update_conformance_matrix(matrix, transport="claude", evidence=_full_evidence(Provenance.WIRE))
    assert set(matrix) == {"agy", "claude"}
    assert all(ev.provenance is Provenance.TRANSCRIPT for ev in matrix["agy"].values())
    assert all(ev.provenance is Provenance.WIRE for ev in matrix["claude"].values())


def test_update_conformance_matrix_a_later_run_replaces_the_earlier_row_wholesale() -> None:
    matrix = update_conformance_matrix({}, transport="agy", evidence=_full_evidence(Provenance.HOST_SYNTHESIZED))
    matrix = update_conformance_matrix(matrix, transport="agy", evidence=_full_evidence(Provenance.WIRE))
    assert all(ev.provenance is Provenance.WIRE for ev in matrix["agy"].values())


# ---------------------------------------------------------------------------
# render_conformance_matrix_markdown: one row per configured transport,
# fact-by-fact grade (the plan's exact Verify expectation for S-4).
# ---------------------------------------------------------------------------


def test_render_conformance_matrix_markdown_has_one_row_per_transport_with_fact_by_fact_grade() -> None:
    matrix = {
        "agy": _full_evidence(Provenance.TRANSCRIPT),
        "claude": _full_evidence(Provenance.WIRE),
    }
    rendered = render_conformance_matrix_markdown(matrix)
    lines = rendered.splitlines()
    agy_row = next(line for line in lines if line.startswith("| agy "))
    claude_row = next(line for line in lines if line.startswith("| claude "))
    for fact in CONFORMANCE_MATRIX_FACTS:
        assert fact in rendered  # header names every required contract fact
    assert "TRANSCRIPT" in agy_row
    assert "WIRE" in claude_row
    assert "WIRE" not in agy_row
    assert "TRANSCRIPT" not in claude_row


def test_render_conformance_matrix_markdown_orders_rows_by_the_canonical_transport_order() -> None:
    matrix = {
        "opencode": _full_evidence(Provenance.WIRE),
        "agy": _full_evidence(Provenance.WIRE),
        "cursor": _full_evidence(Provenance.WIRE),
    }
    rendered = render_conformance_matrix_markdown(matrix)
    assert rendered.index("| agy ") < rendered.index("| cursor ") < rendered.index("| opencode ")
    # matches the brief's canonical F4 ordering (the five transports with a
    # registered `ralph smoke-interactive-*` CLI command).
    assert CONFORMANCE_MATRIX_TRANSPORT_ORDER == (
        "claude",
        "agy",
        "nanocoder",
        "cursor",
        "opencode",
    )


def test_canonical_tuple_matches_registered_smoke_commands() -> None:
    """The canonical transport tuple must equal the set of smoke-capable CLI commands.

    Regression that locks in S-11's invariant: ``CONFORMANCE_MATRIX_TRANSPORT_ORDER``
    is the single source of truth for the matrix's row/column order, and
    must equal the set of transports with a registered
    ``ralph smoke-interactive-*`` CLI command in ``ralph/cli/main.py``.
    Without this regression a future contributor could silently let
    ``codex`` or ``pi`` (no smoke command, can never be populated by a
    real run) back into the canonical tuple, or drop ``nanocoder`` /
    ``opencode`` (have smoke commands and can).
    """
    from ralph.cli.main import app  # local import: keeps the regression
    # focused on the matrix invariant and the CLI command registry.

    # Strip the ``smoke-interactive-`` prefix so we compare transport
    # names (``claude``) against the canonical tuple rather than
    # full CLI command names (``smoke-interactive-claude``).
    registered_smoke_transports = {
        cmd.name.removeprefix("smoke-interactive-")
        for cmd in app.registered_commands
        if getattr(cmd, "name", None) and cmd.name.startswith("smoke-interactive-")
    }

    expected_canonical = {
        "claude",
        "agy",
        "nanocoder",
        "cursor",
        "opencode",
    }
    assert set(CONFORMANCE_MATRIX_TRANSPORT_ORDER) == expected_canonical
    assert set(CONFORMANCE_MATRIX_TRANSPORT_ORDER) == registered_smoke_transports, (
        "CONFORMANCE_MATRIX_TRANSPORT_ORDER must equal the set of "
        "transports with a registered `ralph smoke-interactive-*` CLI "
        f"command. canonical={set(CONFORMANCE_MATRIX_TRANSPORT_ORDER)} "
        f"registered={registered_smoke_transports}"
    )
    # ``codex`` and ``pi`` must NOT appear in the canonical tuple --
    # they have no smoke command and can never be populated by a real run.
    for invalid in ("codex", "pi"):
        assert invalid not in CONFORMANCE_MATRIX_TRANSPORT_ORDER, (
            f"{invalid!r} is not smoke-capable and must not appear in "
            f"the canonical transport tuple"
        )


def test_render_conformance_matrix_markdown_names_absent_for_an_unrecorded_fact() -> None:
    matrix = {"agy": {"tool_activity_seen": _wire("14 frames")}}
    rendered = render_conformance_matrix_markdown(matrix)
    agy_row = next(line for line in rendered.splitlines() if line.startswith("| agy "))
    assert "ABSENT" in agy_row  # never blank -- an unproven fact must stay visible


def test_render_conformance_matrix_markdown_reports_no_runs_recorded_yet_for_an_empty_matrix() -> None:
    assert "No smoke runs recorded yet." in render_conformance_matrix_markdown({})


# ---------------------------------------------------------------------------
# record_conformance_matrix: the impure load -> update -> persist pipeline,
# exercised entirely through an in-memory FileBackend.
# ---------------------------------------------------------------------------


def test_record_conformance_matrix_persists_json_and_markdown(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    workspace_root = tmp_path_factory.mktemp("smoke-matrix-ws")
    backend = _MemoryBackend()
    md_path = record_conformance_matrix(
        workspace_root,
        transport="agy",
        evidence=_full_evidence(Provenance.TRANSCRIPT),
        backend=backend,
    )
    json_path, expected_md_path = conformance_matrix_paths(workspace_root)
    assert md_path == expected_md_path
    assert backend.exists(json_path)
    assert backend.exists(md_path)
    assert "agy" in backend.read_text(md_path)


def test_record_conformance_matrix_accumulates_rows_across_separate_runs(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Two separate (expensive, manual) smoke runs for different transports
    both land in the SAME durable matrix -- the whole point of F4 is that
    the matrix is a cross-transport artefact, not a per-run scratch file.
    """
    workspace_root = tmp_path_factory.mktemp("smoke-matrix-ws")
    backend = _MemoryBackend()
    record_conformance_matrix(
        workspace_root, transport="agy", evidence=_full_evidence(Provenance.TRANSCRIPT), backend=backend
    )
    md_path = record_conformance_matrix(
        workspace_root, transport="claude", evidence=_full_evidence(Provenance.WIRE), backend=backend
    )
    json_path, _ = conformance_matrix_paths(workspace_root)
    matrix = load_conformance_matrix(json_path, backend=backend)
    assert set(matrix) == {"agy", "claude"}
    rendered = backend.read_text(md_path)
    assert "| agy " in rendered
    assert "| claude " in rendered


def test_record_conformance_matrix_a_rerun_of_the_same_transport_replaces_its_row(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    workspace_root = tmp_path_factory.mktemp("smoke-matrix-ws")
    backend = _MemoryBackend()
    record_conformance_matrix(
        workspace_root, transport="agy", evidence=_full_evidence(Provenance.HOST_SYNTHESIZED), backend=backend
    )
    record_conformance_matrix(
        workspace_root, transport="agy", evidence=_full_evidence(Provenance.WIRE), backend=backend
    )
    json_path, _ = conformance_matrix_paths(workspace_root)
    matrix = load_conformance_matrix(json_path, backend=backend)
    assert set(matrix) == {"agy"}
    assert all(ev.provenance is Provenance.WIRE for ev in matrix["agy"].values())


def test_load_conformance_matrix_is_fail_open_for_a_missing_file(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    workspace_root = tmp_path_factory.mktemp("smoke-matrix-ws")
    json_path, _ = conformance_matrix_paths(workspace_root)
    assert load_conformance_matrix(json_path, backend=_MemoryBackend()) == {}


def test_load_conformance_matrix_is_fail_open_for_malformed_json(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    workspace_root = tmp_path_factory.mktemp("smoke-matrix-ws")
    json_path, _ = conformance_matrix_paths(workspace_root)
    backend = _MemoryBackend()
    backend.write_text(json_path, "{not valid json")
    assert load_conformance_matrix(json_path, backend=backend) == {}


# ---------------------------------------------------------------------------
# Capability matrix (S-4 / S-6): the per-agent tri-state declarations
# are appended to the markdown sibling of the conformance matrix so an
# operator can read what each agent declares without reading code.
# ---------------------------------------------------------------------------


def test_render_capability_matrix_markdown_renders_one_row_per_builtin_agent() -> None:
    rows = {support.name: support.display_capabilities for support in builtin_supports()}
    rendered = render_capability_matrix_markdown(capability_rows=rows)
    for support in builtin_supports():
        assert f"| {support.name} " in rendered, (
            f"Capability matrix missing row for built-in {support.name!r}"
        )


def test_render_capability_matrix_markdown_lists_capability_columns_in_order() -> None:
    """Header columns must match the catalog-derived vocabulary, in declared order."""
    rows = {support.name: support.display_capabilities for support in builtin_supports()}
    rendered = render_capability_matrix_markdown(capability_rows=rows)
    header = next(line for line in rendered.splitlines() if line.startswith("| Agent "))
    capability_names = [c.name for c in all_display_capabilities()]
    for capability_name in capability_names:
        assert capability_name in header


def test_render_capability_matrix_markdown_orders_rows_by_canonical_agent_order() -> None:
    """Row order must follow CANONICAL_CAPABILITY_AGENT_ORDER so operators can scan it."""
    rows = {support.name: support.display_capabilities for support in builtin_supports()}
    rendered = render_capability_matrix_markdown(capability_rows=rows)
    body_rows = [
        line
        for line in rendered.splitlines()
        if line.startswith("| ")
        and not line.startswith("| Agent ")
        and not line.startswith("| ---")
    ]
    observed_order = [row.split("|")[1].strip() for row in body_rows]
    expected = [name for name in CANONICAL_CAPABILITY_AGENT_ORDER if name in rows]
    expected.extend(sorted(name for name in rows if name not in CANONICAL_CAPABILITY_AGENT_ORDER))
    assert observed_order == expected


def test_render_capability_matrix_markdown_inlines_unsupported_reasons() -> None:
    """The matrix render surfaces the reason text for non-SUPPORTED stances."""
    rows = {
        "opencode": (
            DisplayCapabilityStance.unimplemented(
                DisplayCapability.SYNTAX_HIGHLIGHTING,
                reason="OpenCode 1.18.14 wire format drift not yet measured",
            ),
            DisplayCapabilityStance.unimplemented(
                DisplayCapability.FILE_PREVIEW,
                reason="OpenCode 1.18.14 wire format drift not yet measured",
            ),
            DisplayCapabilityStance.unimplemented(
                DisplayCapability.EDIT_DIFF,
                reason="OpenCode 1.18.14 wire format drift not yet measured",
            ),
        ),
    }
    rendered = render_capability_matrix_markdown(capability_rows=rows)
    assert "UNIMPLEMENTED (OpenCode 1.18.14 wire format drift not yet measured)" in rendered


def test_render_capability_matrix_markdown_handles_empty_rows() -> None:
    rendered = render_capability_matrix_markdown(capability_rows={})
    assert "No built-in capability declarations recorded yet." in rendered


def test_record_conformance_matrix_persists_capability_table_too(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The markdown sibling carries both the evidence table AND the capability table."""
    workspace_root = tmp_path_factory.mktemp("smoke-matrix-ws")
    backend = _MemoryBackend()
    md_path = record_conformance_matrix(
        workspace_root,
        transport="agy",
        evidence=_full_evidence(Provenance.TRANSCRIPT),
        backend=backend,
    )
    rendered = backend.read_text(md_path)
    assert "## Display-capability declarations (S-4)" in rendered
    for support in builtin_supports():
        assert f"| {support.name} " in rendered
