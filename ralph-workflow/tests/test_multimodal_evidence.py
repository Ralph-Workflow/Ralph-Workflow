"""Tests for the multimodal smoke scenario module (S-2 / criterion 5).

These tests pin the contract four facts together:

1. The :func:`build_smoke_fixture_png` builder emits a valid PNG whose
   IHDR reports the requested geometry and whose payload always
   exceeds the inline-size cap (:data:`SMOKE_MEDIA_MAX_INLINE_BYTES`),
   so every harness identity takes the handle-mint path the
   multimodal grader grades.
2. :func:`params_digest` reproduces the digest stored on an existing
   wire-ledger row, so the replay-hop assertion can match what the
   server actually signed.
3. :func:`grade_multimodal_evidence` returns ``WIRE`` only when ALL
   four contract conditions hold (verified ``read_media`` call,
   server-persisted ``MEDIA_RECEIPT`` equal to the agent's written
   token, verified replay-digest call matching the server-minted
   handle, geometry and sha256 match the fixture).
4. The poisoned-response case -- a stub that dials the media endpoint
   but discards the response and fabricates the receipt -- grades
   ``WORKSPACE_EFFECT``, not ``WIRE``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ralph.mcp.server._wire_ledger import (
    append_wire_record,
    params_digest,
    verify_chain,
    wire_evidence_for,
)
from ralph.pipeline.plumbing.smoke_evidence import Provenance
from ralph.pipeline.plumbing.smoke_multimodal import (
    SMOKE_FIXTURE_RELNAME,
    SMOKE_MEDIA_MAX_INLINE_BYTES,
    build_smoke_fixture_png,
    expected_fixture_sha256,
    expected_replay_params,
    generate_fixture_geometry,
    grade_multimodal_evidence,
    multimodal_prompt_requirements,
    read_media_registry_for_fixture,
    smoke_media_config_toml,
)


def _png_ihdr_geometry(raw: bytes) -> tuple[int, int]:
    """Return the (width, height) reported by a PNG's IHDR chunk.

    Mirrors the parser in :mod:`ralph.mcp.tools.workspace._media_handlers`
    and validates the fixture builder did not corrupt the IHDR.
    """
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "fixture must start with the PNG signature"
    assert raw[12:16] == b"IHDR", "first chunk must be IHDR"
    width = int.from_bytes(raw[16:20], byteorder="big", signed=False)
    height = int.from_bytes(raw[20:24], byteorder="big", signed=False)
    return width, height


class TestSmokeFixtureBuilder:
    """``build_smoke_fixture_png`` emits a valid PNG that always exceeds the inline cap."""

    @pytest.mark.parametrize(
        "geometry",
        [(24, 24), (32, 32), (48, 24), (60, 40)],
    )
    def test_fixture_payload_exceeds_inline_cap(self, geometry: tuple[int, int]) -> None:
        """Every realistic smoke-geometry must exceed ``SMOKE_MEDIA_MAX_INLINE_BYTES``.

        Pinning this keeps the handle-mint path uniform across all six
        harness identities -- the inline cap is the only thing that
        distinguishes inline-image delivery from the resource-reference
        replay path the grader depends on. Geometries below 24x24 are
        not in the per-run draw range and would short-circuit inline
        delivery, so we do not require them to cross the cap.
        """
        width, height = geometry
        payload = build_smoke_fixture_png(width, height)
        assert len(payload) > SMOKE_MEDIA_MAX_INLINE_BYTES

    def test_fixture_ihdr_reports_requested_geometry(self) -> None:
        """The IHDR chunk reports the same (width, height) the builder was called with."""
        payload = build_smoke_fixture_png(43, 29)
        assert _png_ihdr_geometry(payload) == (43, 29)

    @pytest.mark.parametrize("dim", [-1, 0])
    def test_rejects_non_positive_dimensions(self, dim: int) -> None:
        with pytest.raises(ValueError):
            build_smoke_fixture_png(dim, 32)
        with pytest.raises(ValueError):
            build_smoke_fixture_png(32, dim)

    def test_fixture_sha256_is_deterministic_for_fixed_geometry(self) -> None:
        """The fixture's SHA-256 is fully determined by ``width`` and ``height``."""
        first = build_smoke_fixture_png(40, 24)
        second = build_smoke_fixture_png(40, 24)
        assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
        assert expected_fixture_sha256(40, 24) == hashlib.sha256(first).hexdigest()

    def test_fixture_sha256_changes_with_geometry(self) -> None:
        """Different geometries must produce different SHA-256 digests."""
        assert expected_fixture_sha256(40, 24) != expected_fixture_sha256(24, 40)


class TestParamsDigest:
    """``params_digest`` reproduces the digest stored on an existing row."""

    def test_digest_matches_appended_record(self, tmp_path: Path) -> None:
        record = append_wire_record(
            tmp_path,
            method="tools/call",
            tool_name="read_media",
            params={"path": "smoke-fixture.png", "format": "inline"},
            run_id="run-1",
            secret="s3cr3t",
        )
        assert record is not None
        assert record.params_digest == params_digest(
            {"path": "smoke-fixture.png", "format": "inline"}
        )

    def test_digest_is_stable_under_key_order(self) -> None:
        """Identical parameter sets with reordered keys must produce identical digests."""
        left = params_digest({"path": "a", "format": "b"})
        right = params_digest({"format": "b", "path": "a"})
        assert left == right

    def test_wire_evidence_filters_by_params_digest(self, tmp_path: Path) -> None:
        """``wire_evidence_for`` accepts the new ``params_digest=`` filter."""
        first_record = append_wire_record(
            tmp_path,
            method="tools/call",
            tool_name="read_media",
            params={"path": "smoke-fixture.png"},
            run_id="run-1",
            secret="s3cr3t",
        )
        replay_digest = params_digest({"path": "ralph://media/abc", "format": "inline"})
        append_wire_record(
            tmp_path,
            method="tools/call",
            tool_name="read_media",
            params={"path": "ralph://media/abc", "format": "inline"},
            run_id="run-1",
            secret="s3cr3t",
        )
        assert first_record is not None
        assert verify_chain(tmp_path, "s3cr3t") is True
        assert (
            wire_evidence_for(
                tmp_path,
                "run-1",
                tool_name="read_media",
                secret="s3cr3t",
                params_digest=replay_digest,
            )
            is True
        )
        # A different params digest must NOT match.
        assert (
            wire_evidence_for(
                tmp_path,
                "run-1",
                tool_name="read_media",
                secret="s3cr3t",
                params_digest="0" * 64,
            )
            is False
        )


class TestMediaRegistryLookup:
    """``read_media_registry_for_fixture`` reads what the server wrote."""

    def test_returns_none_when_registry_missing(self, tmp_path: Path) -> None:
        assert read_media_registry_for_fixture(tmp_path, SMOKE_FIXTURE_RELNAME) is None

    def test_returns_entry_when_present(self, tmp_path: Path) -> None:
        registry_dir = tmp_path / ".agent/tmp"
        registry_dir.mkdir(parents=True)
        registry_path = tmp_path / ".agent/tmp/media_registry.json"
        registry_path.write_text(
            json.dumps(
                {
                    "schema_version": "2",
                    "artifacts": [
                        {
                            "artifact_id": "abc",
                            "uri": "ralph://media/abc",
                            "mime_type": "image/png",
                            "title": "smoke-fixture.png",
                            "modality": "image",
                            "delivery": "resource_reference_replay",
                            "reason": "smoke",
                            "source_path": SMOKE_FIXTURE_RELNAME,
                            "cache_path": ".agent/tmp/media/abc",
                            "source_uri": "",
                            "block_type": "",
                            "identity_key": "smoke-fixture.png",
                            "failure_kind": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        entry = read_media_registry_for_fixture(tmp_path, SMOKE_FIXTURE_RELNAME)
        assert entry is not None
        assert entry["uri"] == "ralph://media/abc"


class TestGradeMultimodalEvidence:
    """``grade_multimodal_evidence`` enforces the four-condition contract."""

    @staticmethod
    def _setup_full_wire_run(
        tmp_path: Path,
        *,
        fixture_relpath: str,
        fixture_size: tuple[int, int],
        include_replay: bool = True,
    ) -> tuple[Path, str]:
        """Materialize the registry + ledger for the full positive contract."""
        width, height = fixture_size
        registry_dir = tmp_path / ".agent/tmp"
        registry_dir.mkdir(parents=True)
        registry_path = tmp_path / ".agent/tmp/media_registry.json"
        uri = "ralph://media/server-minted-uuid"
        registry_path.write_text(
            json.dumps(
                {
                    "schema_version": "2",
                    "artifacts": [
                        {
                            "artifact_id": "server-minted-uuid",
                            "uri": uri,
                            "mime_type": "image/png",
                            "title": "smoke-fixture.png",
                            "modality": "image",
                            "delivery": "resource_reference_replay",
                            "reason": "smoke",
                            "source_path": fixture_relpath,
                            "cache_path": ".agent/tmp/media/server-minted-uuid",
                            "source_uri": "",
                            "block_type": "",
                            "identity_key": f"smoke:{fixture_relpath}",
                            "failure_kind": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        sha256 = expected_fixture_sha256(width, height)
        output_file = tmp_path / "tmp/interactive-claude-smoke/todo-list.js"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            "// smoke output\n"
            f"MEDIA_RECEIPT={uri}\n"
            f"DIMENSIONS={width}x{height}\n"
            f"MEDIA_SHA256={sha256}\n",
            encoding="utf-8",
        )
        secret = "smoke-secret"
        run_id = "interactive-claude-smoke"
        append_wire_record(
            tmp_path,
            method="tools/call",
            tool_name="read_media",
            params={"path": fixture_relpath, "format": "inline"},
            run_id=run_id,
            secret=secret,
        )
        if include_replay:
            replay_digest = params_digest(expected_replay_params(handle=uri))
            append_wire_record(
                tmp_path,
                method="tools/call",
                tool_name="read_media",
                params=expected_replay_params(handle=uri),
                run_id=run_id,
                secret=secret,
            )
            # Sanity: the recorded row carries that exact digest.
            assert wire_evidence_for(
                tmp_path,
                run_id,
                tool_name="read_media",
                secret=secret,
                params_digest=replay_digest,
            ) is True
        return output_file, secret

    def test_full_contract_grades_wire(self, tmp_path: Path) -> None:
        """All four contract conditions grades WIRE."""
        fixture_size = (40, 24)
        output_file, secret = self._setup_full_wire_run(
            tmp_path,
            fixture_relpath=SMOKE_FIXTURE_RELNAME,
            fixture_size=fixture_size,
        )
        evidence = grade_multimodal_evidence(
            tmp_path,
            "interactive-claude-smoke",
            output_file=output_file,
            fixture_relpath=SMOKE_FIXTURE_RELNAME,
            fixture_size=fixture_size,
            secret=secret,
        )
        assert evidence.holds is True
        assert evidence.provenance is Provenance.WIRE, evidence.detail

    def test_missing_replay_record_grades_workspace_effect(self, tmp_path: Path) -> None:
        """The poisoned-response case -- a real ``read_media`` call but no replay hop.

        The stub issued the first ``read_media`` call (leaving a genuine
        verified wire-ledger record), then discarded the response and
        wrote a fabricated receipt. The receipt equals the registry's
        ``uri`` but there is NO second ``read_media`` call carrying the
        replay handle, so the agent's claim of "I consumed the
        response" is not actually proven -- the run grades
        ``WORKSPACE_EFFECT``, NOT ``WIRE``.
        """
        fixture_size = (40, 24)
        output_file, secret = self._setup_full_wire_run(
            tmp_path,
            fixture_relpath=SMOKE_FIXTURE_RELNAME,
            fixture_size=fixture_size,
            include_replay=False,
        )
        evidence = grade_multimodal_evidence(
            tmp_path,
            "interactive-claude-smoke",
            output_file=output_file,
            fixture_relpath=SMOKE_FIXTURE_RELNAME,
            fixture_size=fixture_size,
            secret=secret,
        )
        assert evidence.holds is False
        assert evidence.provenance is Provenance.WORKSPACE_EFFECT, evidence.detail
        assert "replay" in evidence.detail.lower()

    def test_fabricated_receipt_grades_workspace_effect(self, tmp_path: Path) -> None:
        """An agent that writes a fabricated ``MEDIA_RECEIPT`` (not in the registry) grades below WIRE."""
        fixture_size = (40, 24)
        width, height = fixture_size
        registry_dir = tmp_path / ".agent/tmp"
        registry_dir.mkdir(parents=True)
        registry_path = tmp_path / ".agent/tmp/media_registry.json"
        registry_path.write_text(
            json.dumps(
                {
                    "schema_version": "2",
                    "artifacts": [
                        {
                            "artifact_id": "server-minted-uuid",
                            "uri": "ralph://media/server-minted-uuid",
                            "mime_type": "image/png",
                            "title": "smoke-fixture.png",
                            "modality": "image",
                            "delivery": "resource_reference_replay",
                            "reason": "smoke",
                            "source_path": SMOKE_FIXTURE_RELNAME,
                            "cache_path": ".agent/tmp/media/server-minted-uuid",
                            "source_uri": "",
                            "block_type": "",
                            "identity_key": "smoke",
                            "failure_kind": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        sha256 = expected_fixture_sha256(width, height)
        output_file = tmp_path / "tmp/interactive-claude-smoke/todo-list.js"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            "// smoke output\n"
            "MEDIA_RECEIPT=ralph://media/fabricated-uuid\n"
            f"DIMENSIONS={width}x{height}\n"
            f"MEDIA_SHA256={sha256}\n",
            encoding="utf-8",
        )
        secret = "smoke-secret"
        run_id = "interactive-claude-smoke"
        append_wire_record(
            tmp_path,
            method="tools/call",
            tool_name="read_media",
            params={"path": SMOKE_FIXTURE_RELNAME, "format": "inline"},
            run_id=run_id,
            secret=secret,
        )
        append_wire_record(
            tmp_path,
            method="tools/call",
            tool_name="read_media",
            params={"path": "ralph://media/server-minted-uuid", "format": "inline"},
            run_id=run_id,
            secret=secret,
        )
        evidence = grade_multimodal_evidence(
            tmp_path,
            run_id,
            output_file=output_file,
            fixture_relpath=SMOKE_FIXTURE_RELNAME,
            fixture_size=fixture_size,
            secret=secret,
        )
        assert evidence.holds is False
        assert evidence.provenance is Provenance.WORKSPACE_EFFECT, evidence.detail
        assert "fabricated" in evidence.detail.lower() or "match" in evidence.detail.lower()

    def test_no_token_line_grades_absent(self, tmp_path: Path) -> None:
        """When the agent never writes a ``MEDIA_RECEIPT=`` line the fact is absent."""
        fixture_size = (40, 24)
        output_file = tmp_path / "tmp/interactive-claude-smoke/todo-list.js"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("// no tokens here\n", encoding="utf-8")
        evidence = grade_multimodal_evidence(
            tmp_path,
            "interactive-claude-smoke",
            output_file=output_file,
            fixture_relpath=SMOKE_FIXTURE_RELNAME,
            fixture_size=fixture_size,
            secret="smoke-secret",
        )
        assert evidence.holds is False
        assert evidence.provenance is Provenance.ABSENT

    def test_no_media_call_no_registry_grades_workspace_effect(self, tmp_path: Path) -> None:
        """A workspace with only tokens (no real ``read_media`` call, no registry entry) grades below WIRE."""
        fixture_size = (40, 24)
        width, height = fixture_size
        output_file = tmp_path / "tmp/interactive-claude-smoke/todo-list.js"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            "// only tokens, no real read_media call\n"
            "MEDIA_RECEIPT=ralph://media/never-issued\n"
            f"DIMENSIONS={width}x{height}\n"
            f"MEDIA_SHA256={expected_fixture_sha256(width, height)}\n",
            encoding="utf-8",
        )
        evidence = grade_multimodal_evidence(
            tmp_path,
            "interactive-claude-smoke",
            output_file=output_file,
            fixture_relpath=SMOKE_FIXTURE_RELNAME,
            fixture_size=fixture_size,
            secret="smoke-secret",
        )
        assert evidence.holds is False
        assert evidence.provenance is Provenance.WORKSPACE_EFFECT, evidence.detail


class TestPromptRequirements:
    """``multimodal_prompt_requirements`` carries every required bullet."""

    def test_prompt_carries_required_tokens(self) -> None:
        prompt = multimodal_prompt_requirements(SMOKE_FIXTURE_RELNAME)
        assert SMOKE_FIXTURE_RELNAME in prompt
        assert "MEDIA_RECEIPT" in prompt
        assert "DIMENSIONS" in prompt
        assert "MEDIA_SHA256" in prompt
        assert "read_media" in prompt
        assert "read_image" in prompt
        assert "ralph://media" in prompt
        assert "params_digest" in prompt or "second, fresh tool call" in prompt

    def test_config_toml_pins_inline_cap(self) -> None:
        """The harness's mcp.toml fragment pins the inline cap low enough that every harness identity takes the handle-mint path."""
        config = smoke_media_config_toml()
        assert "[media]" in config
        assert f"max_inline_bytes = {SMOKE_MEDIA_MAX_INLINE_BYTES}" in config


class TestGenerateFixtureGeometry:
    """The geometry RNG makes the expected answer exist nowhere in the repo."""

    def test_generate_returns_width_and_height_in_range(self) -> None:
        for _ in range(10):
            width, height = generate_fixture_geometry()
            assert 24 <= width <= 63
            assert 24 <= height <= 63

    def test_generate_does_not_expose_137x89(self) -> None:
        """The deleted fixed geometry must never be returned by the RNG."""
        for _ in range(50):
            width, height = generate_fixture_geometry()
            assert (width, height) != (137, 89), (
                "the fixed 137x89 geometry was deleted; the RNG must not return it"
            )

    def test_geometry_is_at_least_24x24(self) -> None:
        """The minimum geometry keeps the fixture comfortably above the inline cap."""
        width, height = generate_fixture_geometry()
        payload = build_smoke_fixture_png(width, height)
        assert len(payload) > SMOKE_MEDIA_MAX_INLINE_BYTES
