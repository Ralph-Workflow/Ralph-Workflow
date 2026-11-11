from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ralph.mcp.server._wire_ledger import (
    _ledger_path,
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
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "fixture must start with the PNG signature"
    assert raw[12:16] == b"IHDR", "first chunk must be IHDR"
    width = int.from_bytes(raw[16:20], byteorder="big", signed=False)
    height = int.from_bytes(raw[20:24], byteorder="big", signed=False)
    return width, height


class TestSmokeFixtureBuilder:
    @pytest.mark.parametrize(
        "geometry",
        [(24, 24), (32, 32), (48, 24), (60, 40)],
    )
    def test_fixture_payload_exceeds_inline_cap(self, geometry: tuple[int, int]) -> None:
        width, height = geometry
        payload = build_smoke_fixture_png(width, height)
        assert len(payload) > SMOKE_MEDIA_MAX_INLINE_BYTES

    def test_fixture_ihdr_reports_requested_geometry(self) -> None:
        payload = build_smoke_fixture_png(43, 29)
        assert _png_ihdr_geometry(payload) == (43, 29)

    @pytest.mark.parametrize("dim", [-1, 0])
    def test_rejects_non_positive_dimensions(self, dim: int) -> None:
        with pytest.raises(ValueError):
            build_smoke_fixture_png(dim, 32)
        with pytest.raises(ValueError):
            build_smoke_fixture_png(32, dim)

    def test_fixture_sha256_is_deterministic_for_fixed_geometry(self) -> None:
        first = build_smoke_fixture_png(40, 24)
        second = build_smoke_fixture_png(40, 24)
        assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
        assert expected_fixture_sha256(40, 24) == hashlib.sha256(first).hexdigest()

    def test_fixture_sha256_changes_with_geometry(self) -> None:
        assert expected_fixture_sha256(40, 24) != expected_fixture_sha256(24, 40)


class TestParamsDigest:
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
        left = params_digest({"path": "a", "format": "b"})
        right = params_digest({"format": "b", "path": "a"})
        assert left == right

    def test_wire_evidence_filters_by_params_digest(self, tmp_path: Path) -> None:
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
    def test_returns_none_when_registry_missing(self, tmp_path: Path) -> None:
        assert read_media_registry_for_fixture(tmp_path, SMOKE_FIXTURE_RELNAME) is None

    def test_returns_most_recent_entry_when_fixture_has_prior_runs(self, tmp_path: Path) -> None:
        registry_dir = tmp_path / ".agent/tmp"
        registry_dir.mkdir(parents=True)
        registry_path = tmp_path / ".agent/tmp/media_registry.json"
        registry_path.write_text(
            json.dumps(
                {
                    "schema_version": "2",
                    "artifacts": [
                        {
                            "artifact_id": "prior-run",
                            "uri": "ralph://media/prior-run",
                            "mime_type": "image/png",
                            "title": "smoke-fixture.png",
                            "modality": "image",
                            "source_path": SMOKE_FIXTURE_RELNAME,
                        },
                        {
                            "artifact_id": "current-run",
                            "uri": "ralph://media/current-run",
                            "mime_type": "image/png",
                            "title": "smoke-fixture.png",
                            "modality": "image",
                            "source_path": SMOKE_FIXTURE_RELNAME,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        entry = read_media_registry_for_fixture(tmp_path, SMOKE_FIXTURE_RELNAME)

        assert entry is not None
        assert entry["uri"] == "ralph://media/current-run"

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
    @staticmethod
    def _setup_full_wire_run(
        tmp_path: Path,
        *,
        fixture_relpath: str,
        fixture_size: tuple[int, int],
        include_replay: bool = True,
        replay_tool_name: str = "read_media",
    ) -> tuple[Path, str]:
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
        perception_secret = "ab" * 16
        (tmp_path / fixture_relpath).write_bytes(
            build_smoke_fixture_png(width, height, perception_secret=perception_secret)
        )
        output_file.write_text(
            "// smoke output\n"
            f"MEDIA_RECEIPT={uri}\n"
            f"DIMENSIONS={width}x{height}\n"
            f"MEDIA_SHA256={sha256}\n"
            f"PERCEPTION_SECRET={perception_secret}\n",
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
            delivery_mode="inline_image",
        )
        if include_replay:
            replay_digest = params_digest(expected_replay_params(handle=uri))
            append_wire_record(
                tmp_path,
                method="tools/call",
                tool_name=replay_tool_name,
                params=expected_replay_params(handle=uri),
                run_id=run_id,
                secret=secret,
                delivery_mode="inline_image",
            )
            assert (
                wire_evidence_for(
                    tmp_path,
                    run_id,
                    tool_name=replay_tool_name,
                    secret=secret,
                    params_digest=replay_digest,
                )
                is True
            )
        return output_file, secret

    def test_full_contract_grades_wire(self, tmp_path: Path) -> None:
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

    def test_read_image_replay_hop_grades_wire(self, tmp_path: Path) -> None:
        fixture_size = (40, 24)
        output_file, secret = self._setup_full_wire_run(
            tmp_path,
            fixture_relpath=SMOKE_FIXTURE_RELNAME,
            fixture_size=fixture_size,
            replay_tool_name="read_image",
        )

        evidence = grade_multimodal_evidence(
            tmp_path,
            "interactive-claude-smoke",
            output_file=output_file,
            fixture_relpath=SMOKE_FIXTURE_RELNAME,
            fixture_size=fixture_size,
            secret=secret,
        )

        assert evidence.provenance is Provenance.WIRE, evidence.detail

    def test_ccs_text_handle_contract_does_not_require_pixel_secret(self, tmp_path: Path) -> None:
        fixture_size = (40, 24)
        output_file, secret = self._setup_full_wire_run(
            tmp_path,
            fixture_relpath=SMOKE_FIXTURE_RELNAME,
            fixture_size=fixture_size,
        )
        output_file.write_text(
            output_file.read_text(encoding="utf-8").replace(
                "PERCEPTION_SECRET=abababababababababababababababab\\n", ""
            ),
            encoding="utf-8",
        )

        evidence = grade_multimodal_evidence(
            tmp_path,
            "interactive-claude-smoke",
            output_file=output_file,
            fixture_relpath=SMOKE_FIXTURE_RELNAME,
            fixture_size=fixture_size,
            secret=secret,
            require_perception_secret=False,
        )

        assert evidence.provenance is Provenance.WIRE, evidence.detail

    def test_full_contract_without_pixel_secret_does_not_grade_wire(self, tmp_path: Path) -> None:
        fixture_size = (40, 24)
        output_file, secret = self._setup_full_wire_run(
            tmp_path,
            fixture_relpath=SMOKE_FIXTURE_RELNAME,
            fixture_size=fixture_size,
        )
        output_file.write_text(
            output_file.read_text(encoding="utf-8").replace(
                "PERCEPTION_SECRET=abababababababababababababababab\n", ""
            ),
            encoding="utf-8",
        )

        evidence = grade_multimodal_evidence(
            tmp_path,
            "interactive-claude-smoke",
            output_file=output_file,
            fixture_relpath=SMOKE_FIXTURE_RELNAME,
            fixture_size=fixture_size,
            secret=secret,
        )

        assert evidence.provenance is not Provenance.WIRE
        assert "PERCEPTION_SECRET" in evidence.detail

    def test_missing_replay_record_grades_workspace_effect(self, tmp_path: Path) -> None:
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
    def test_prompt_carries_required_tokens(self) -> None:
        prompt = multimodal_prompt_requirements(SMOKE_FIXTURE_RELNAME)
        assert SMOKE_FIXTURE_RELNAME in prompt
        assert "MEDIA_RECEIPT" in prompt
        assert "DIMENSIONS" in prompt
        assert "MEDIA_SHA256" in prompt
        assert "read_media" in prompt
        assert "read_image" in prompt
        assert "ralph://media" in prompt
        assert 'format="inline"' in prompt
        assert "params_digest" in prompt or "second, fresh tool call" in prompt

    def test_config_toml_pins_inline_cap(self) -> None:
        config = smoke_media_config_toml()
        assert "[media]" in config
        assert f"max_inline_bytes = {SMOKE_MEDIA_MAX_INLINE_BYTES}" in config


class TestGenerateFixtureGeometry:
    def test_generate_returns_width_and_height_in_range(self) -> None:
        for _ in range(10):
            width, height = generate_fixture_geometry()
            assert 24 <= width <= 63
            assert 24 <= height <= 63

    def test_generate_does_not_expose_137x89(self) -> None:
        for _ in range(50):
            width, height = generate_fixture_geometry()
            assert (width, height) != (137, 89), (
                "the fixed 137x89 geometry was deleted; the RNG must not return it"
            )

    def test_geometry_is_at_least_24x24(self) -> None:
        width, height = generate_fixture_geometry()
        payload = build_smoke_fixture_png(width, height)
        assert len(payload) > SMOKE_MEDIA_MAX_INLINE_BYTES

    @pytest.mark.parametrize(
        ("rng", "expected_geometry", "lower_bound"),
        [
            (lambda n: 0, (24, 24), SMOKE_MEDIA_MAX_INLINE_BYTES),
            (lambda n: n - 1, (63, 63), SMOKE_MEDIA_MAX_INLINE_BYTES),
        ],
        ids=["lower-bound-24x24", "upper-bound-63x63"],
    )
    def test_fixture_exceeds_inline_cap_at_generator_bounds(
        self,
        rng: object,
        expected_geometry: tuple[int, int],
        lower_bound: int,
    ) -> None:
        width, height = generate_fixture_geometry(rng=rng)
        assert (width, height) == expected_geometry
        payload = build_smoke_fixture_png(width, height)
        assert len(payload) > lower_bound, (
            f"fixture at {expected_geometry} is {len(payload)}B, "
            f"below the {lower_bound}B inline cap; the handle-mint "
            f"path will not be uniformly taken across harness identities"
        )


class TestWireLedgerDeliveryModeMetadata:
    def test_wire_ledger_record_carries_delivery_mode_when_present(self, tmp_path: Path) -> None:
        record = append_wire_record(
            tmp_path,
            method="tools/call",
            tool_name="read_media",
            params={"path": "smoke-fixture.png"},
            run_id="run-1",
            secret="s3cr3t",
            delivery_mode="inline_image",
            provider="claude",
            model_id="claude-opus-4-7",
            agent_id="agent-42",
        )
        assert record is not None
        assert record.delivery_mode == "inline_image"
        assert record.provider == "claude"
        assert record.model_id == "claude-opus-4-7"
        assert record.agent_id == "agent-42"
        assert verify_chain(tmp_path, "s3cr3t") is True
        ledger_path = _ledger_path(tmp_path, "s3cr3t")
        import json as _json

        rows = [
            _json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(rows) == 1
        row = rows[0]
        assert row["delivery_mode"] == "inline_image"
        assert row["provider"] == "claude"
        assert row["model_id"] == "claude-opus-4-7"
        assert row["agent_id"] == "agent-42"

    def test_wire_ledger_record_omits_optional_fields_when_unset(self, tmp_path: Path) -> None:
        record = append_wire_record(
            tmp_path,
            method="tools/call",
            tool_name="read_media",
            params={"path": "smoke-fixture.png"},
            run_id="run-1",
            secret="s3cr3t",
        )
        assert record is not None
        assert record.delivery_mode is None
        assert record.provider is None
        assert record.model_id is None
        assert record.agent_id is None
        assert verify_chain(tmp_path, "s3cr3t") is True
        ledger_path = _ledger_path(tmp_path, "s3cr3t")
        import json as _json

        rows = [
            _json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(rows) == 1
        row = rows[0]
        for field_name in ("delivery_mode", "provider", "model_id", "agent_id"):
            assert field_name not in row, (
                f"unexpected {field_name!r} key in pre-S-6-shape row: {row!r}"
            )
