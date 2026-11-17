from __future__ import annotations

import hashlib
import json
import re
import secrets
import struct
import zlib
from typing import TYPE_CHECKING

from ralph.mcp.server._wire_ledger import (
    _ledger_path,
    params_digest,
    verify_chain,
    wire_evidence_for,
)
from ralph.pipeline.plumbing.smoke_evidence import (
    Evidence,
    Provenance,
    absent,
)
from ralph.prompts.debug_dump import media_registry_path

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

SMOKE_FIXTURE_RELNAME = "smoke-fixture.png"

SMOKE_MEDIA_MAX_INLINE_BYTES = 1024
_PERCEPTION_SECRET_HEX_LENGTH = 32


def build_smoke_fixture_png(
    width: int,
    height: int,
    *,
    perception_secret: str | None = None,
) -> bytes:
    if width <= 0 or height <= 0:
        raise ValueError(f"width and height must be positive, got {width=} {height=}")
    png_signature = b"\x89PNG\r\n\x1a\n"

    def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
        length = struct.pack(">I", len(payload))
        crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        return length + chunk_type + payload + struct.pack(">I", crc)

    ihdr_payload = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_chunk = _chunk(b"IHDR", ihdr_payload)

    salt = hashlib.sha256(f"{width},{height}".encode()).digest()
    raw_scanlines = bytearray()
    perception_secret_bytes: bytes = b""
    if perception_secret is not None:
        try:
            perception_secret_bytes = perception_secret.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError(
                f"perception_secret must be hex-encoded, got {perception_secret!r}"
            ) from exc
        if not re.fullmatch(rb"[0-9a-fA-F]{32}", perception_secret_bytes):
            raise ValueError(
                f"perception_secret must be {_PERCEPTION_SECRET_HEX_LENGTH} hex characters"
            )
    pixel_capacity = width * 3
    for row_index in range(height):
        raw_scanlines.append(0)
        for column_index in range(width * 3):
            raw_scanlines.append((row_index ^ column_index ^ salt[column_index % len(salt)]) & 0xFF)
    if perception_secret_bytes:
        if len(perception_secret_bytes) != _PERCEPTION_SECRET_HEX_LENGTH:
            raise ValueError(
                "perception_secret must contain exactly "
                f"{_PERCEPTION_SECRET_HEX_LENGTH} hex characters"
            )
        last_row_offset = len(raw_scanlines) - (1 + pixel_capacity)
        for secret_index, secret_byte in enumerate(perception_secret_bytes):
            raw_scanlines[last_row_offset + 1 + 4 + secret_index] = secret_byte
    idat_payload = zlib.compress(bytes(raw_scanlines), level=6)
    idat_chunk = _chunk(b"IDAT", idat_payload)

    text_payload = b"Comment\x00" + salt + b"smoke-fixture-salt"
    text_chunk = _chunk(b"tEXt", text_payload)

    iend_chunk = _chunk(b"IEND", b"")

    return png_signature + ihdr_chunk + idat_chunk + text_chunk + iend_chunk


def extract_perception_secret_from_fixture(png_bytes: bytes) -> str | None:
    png_signature_len = 8
    png_ihdr_offset = 16
    if len(png_bytes) < png_signature_len or png_bytes[:png_signature_len] != b"\x89PNG\r\n\x1a\n":
        return None
    if png_bytes[12:png_ihdr_offset] != b"IHDR":
        return None
    width = int.from_bytes(png_bytes[16:20], byteorder="big", signed=False)
    if width <= 0:
        return None
    pixel_capacity = width * 3
    cursor = 8
    idat_payload: bytes | None = None
    while cursor < len(png_bytes):
        if cursor + 8 > len(png_bytes):
            return None
        length = int.from_bytes(png_bytes[cursor : cursor + 4], byteorder="big", signed=False)
        chunk_type = png_bytes[cursor + 4 : cursor + 8]
        chunk_body_start = cursor + 8
        chunk_body_end = chunk_body_start + length
        if chunk_body_end > len(png_bytes):
            return None
        if chunk_type == b"IDAT":
            idat_payload = png_bytes[chunk_body_start:chunk_body_end]
            break
        cursor = chunk_body_end + 4
    if idat_payload is None:
        return None
    try:
        decompressed = zlib.decompress(idat_payload)
    except zlib.error:
        return None
    if len(decompressed) < 1 + pixel_capacity:
        return None
    last_row_offset = len(decompressed) - (1 + pixel_capacity)
    secret_bytes = bytearray()
    for secret_index in range(_PERCEPTION_SECRET_HEX_LENGTH):
        target = last_row_offset + 1 + 4 + secret_index
        if target >= last_row_offset + 1 + pixel_capacity:
            return None
        secret_bytes.append(decompressed[target])
    if not re.fullmatch(rb"[0-9a-fA-F]{32}", bytes(secret_bytes)):
        return None
    return bytes(secret_bytes).decode("ascii")


def generate_fixture_geometry(
    *,
    rng: Callable[[int], int] | None = None,
) -> tuple[int, int]:
    picker = rng if rng is not None else secrets.randbelow
    width = picker(40) + 24
    height = picker(40) + 24
    return width, height


def smoke_media_config_toml() -> str:
    return f"[media]\nmax_inline_bytes = {SMOKE_MEDIA_MAX_INLINE_BYTES}\n"


def multimodal_prompt_requirements(fixture_relpath: str) -> str:
    return (
        "Multimodal contract (DO NOT skip any of these):\n"
        f"- Read the on-disk fixture `{fixture_relpath}` with the media tool this "
        "transport exposes (for example, `read_media` or `read_image` with "
        '`path="' + fixture_relpath + '"`).\n'
        "- The first response will mint a fresh `ralph://media/{artifact_id}` "
        "handle. You MUST take that handle exactly as returned and call "
        "`read_media` (or `read_image`) AGAIN with `path=<that handle>, "
        'format="inline"` as a second, fresh tool call. The replay hop\'s '
        "`params_digest` is the "
        "graded proof you consumed the first response -- skipping it fails "
        "the run, even if a receipt line is written.\n"
        '- Then call `read_image` with `format="metadata"` against the same '
        "fixture (or the handle) and read off the server-computed `width`, "
        "`height`, and `sha256` it returns. These three values are the "
        "ground truth for the output file -- do NOT guess them.\n"
        "- Append exactly these three lines (with NO other formatting or "
        "leading whitespace) to the todo-list.js file at the existing path:\n"
        "  MEDIA_RECEIPT=<the ralph://media/{artifact_id} handle the FIRST response returned>\n"
        "  DIMENSIONS=<width>x<height>\n"
        "  MEDIA_SHA256=<sha256>\n"
        "- Do not fabricate the receipt. If the first response carries a "
        "different handle than the one you write, the run fails.\n"
    )


def read_media_registry_for_fixture(
    workspace_root: Path,
    fixture_relpath: str,
    *,
    expected_uri: str | None = None,
) -> dict[str, str] | None:
    registry_path = workspace_root / media_registry_path()
    if not registry_path.exists():
        return None
    import json

    data: object = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    artifacts_obj: object = data.get("artifacts")
    if not isinstance(artifacts_obj, list):
        return None
    artifacts: list[object] = artifacts_obj
    for entry in reversed(artifacts):
        if not isinstance(entry, dict):
            continue
        entry_source: object = entry.get("source_path")
        entry_uri: object = entry.get("uri")
        if entry_source != fixture_relpath:
            continue
        if expected_uri is not None and entry_uri != expected_uri:
            continue
        coerced: dict[str, str] = {}
        for key in entry:
            value = entry[key]
            if isinstance(value, str):
                coerced[str(key)] = value
            elif isinstance(value, bool | int):
                coerced[str(key)] = str(value)
        return coerced if coerced else None
    return None


def expected_replay_params(*, handle: str) -> dict[str, object]:
    return {"path": handle, "format": "inline"}


def _read_output_token(output_file: Path, key: str) -> str | None:
    if not output_file.exists():
        return None
    try:
        text = output_file.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(f"{key}="):
            value = line[len(f"{key}=") :]
            if value:
                return value
    return None


def _check_receipt_token_shape(receipt_token: str) -> Evidence | None:
    if not receipt_token:
        return absent("MEDIA_RECEIPT=<handle> line was not written to the smoke output file")
    if not receipt_token.startswith("ralph://media/"):
        return Evidence(
            holds=False,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail=(
                f"MEDIA_RECEIPT={receipt_token!r} is not a ralph://media/{{artifact_id}} "
                "handle -- the agent fabricated a receipt instead of using the one "
                "the server minted"
            ),
        )
    return None


def _check_registry_entry(
    workspace_root: Path,
    fixture_relpath: str,
    receipt_token: str,
) -> Evidence | str | None:
    registry_entry = read_media_registry_for_fixture(
        workspace_root,
        fixture_relpath,
        expected_uri=receipt_token,
    )
    if registry_entry is None:
        persisted_entry = read_media_registry_for_fixture(workspace_root, fixture_relpath)
        if persisted_entry is not None:
            server_uri = persisted_entry.get("uri", "")
            return Evidence(
                holds=False,
                provenance=Provenance.WORKSPACE_EFFECT,
                detail=(
                    f"MEDIA_RECEIPT={receipt_token!r} does not match the server-persisted "
                    f"uri {server_uri!r} for {fixture_relpath} -- the agent fabricated the receipt"
                ),
            )
        return Evidence(
            holds=False,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail=(
                "no media_registry.json entry for the fixture; the server never minted a "
                "ralph://media/{artifact_id} handle for this run -- a stub that never "
                "issued the media call cannot grade WIRE"
            ),
        )
    server_uri = registry_entry.get("uri", "")
    if receipt_token != server_uri:
        return Evidence(
            holds=False,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail=(
                f"MEDIA_RECEIPT={receipt_token!r} does not match the server-persisted "
                f"uri {server_uri!r} for {fixture_relpath} -- the agent fabricated the receipt"
            ),
        )
    return server_uri


def _check_broker_chain(workspace_root: Path, secret: str | None) -> Evidence | None:
    if secret is None or not verify_chain(workspace_root, secret):
        return Evidence(
            holds=False,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail=(
                "witness chain does not verify (no RALPH_BROKER_SECRET or broken chain); "
                "the receipt line is present in the output but cannot be attributed to a "
                "verified tools/call record"
            ),
        )
    return None


def grade_multimodal_evidence(
    workspace_root: Path,
    run_id: str,
    *,
    output_file: Path,
    fixture_relpath: str,
    fixture_size: tuple[int, int],
    secret: str | None,
    expected_perception_secret: str | None = None,
    expected_delivery_mode: str | None = None,
    require_perception_secret: bool = True,
) -> Evidence:
    width, height = fixture_size
    receipt_token = _read_output_token(output_file, "MEDIA_RECEIPT")
    shape = _check_receipt_token_shape(receipt_token or "")
    if shape is not None:
        return shape

    server_uri_or_evidence = _check_registry_entry(
        workspace_root, fixture_relpath, receipt_token or ""
    )
    if isinstance(server_uri_or_evidence, Evidence):
        return server_uri_or_evidence
    if server_uri_or_evidence is None:
        return absent(
            "registry check returned no server URI for the fixture; the server never "
            "persisted a ralph://media/{artifact_id} handle for this run"
        )
    server_uri = server_uri_or_evidence

    chain = _check_broker_chain(workspace_root, secret)
    if chain is not None:
        return chain

    present = _check_media_call_record(workspace_root, run_id, secret)
    if present is not None:
        return present

    replay_check = _check_replay_record(workspace_root, run_id, secret, server_uri)
    if replay_check is not None:
        return replay_check

    geometry = _check_geometry_match(output_file, width, height)
    if geometry is not None:
        return geometry

    if require_perception_secret:
        if expected_perception_secret is None:
            try:
                expected_perception_secret = extract_perception_secret_from_fixture(
                    (workspace_root / fixture_relpath).read_bytes()
                )
            except OSError:
                expected_perception_secret = None
        if expected_perception_secret is None:
            return Evidence(
                holds=False,
                provenance=Provenance.WORKSPACE_EFFECT,
                detail="fixture has no readable pixel-only PERCEPTION_SECRET",
            )
        secret_check = _check_perception_secret_token(output_file, expected_perception_secret)
        if secret_check is not None:
            return secret_check
        delivery_check = _check_delivery_mode_perceptible(
            workspace_root, run_id, secret, expected_delivery_mode
        )
        if delivery_check is not None:
            return delivery_check

    return Evidence(
        holds=True,
        provenance=Provenance.WIRE,
        detail=(
            "verified read_media + verified replay-hop call + server-persisted "
            "MEDIA_RECEIPT equal to MEDIA_SHA256-grade geometry from the fixture"
            + (
                " + PERCEPTION_SECRET=<hex> token carried by the agent"
                " + delivery_mode perceptible (inline_image/typed_block)"
                if require_perception_secret
                else ""
            )
        ),
    )


_PERCEPTIBLE_DELIVERY_MODES: frozenset[str] = frozenset({"inline_image", "typed_block"})


def _wire_ledger_records_for_run(
    workspace_root: Path,
    run_id: str,
    secret: str | None,
) -> tuple[dict[str, object], ...]:
    ledger_path = _ledger_path(workspace_root, secret)
    if not ledger_path.exists():
        return ()
    rows: list[dict[str, object]] = []
    try:
        raw = ledger_path.read_text(encoding="utf-8")
    except OSError:
        return ()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            loaded: object = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(loaded, dict):
            continue
        row: dict[str, object] = loaded
        run_id_value: object = row.get("run_id")
        if isinstance(run_id_value, str) and run_id_value == run_id:
            rows.append(dict(row))
    return tuple(rows)


def _check_delivery_mode_perceptible(
    workspace_root: Path,
    run_id: str,
    secret: str | None,
    expected_delivery_mode: str | None,
) -> Evidence | None:
    rows = _wire_ledger_records_for_run(workspace_root, run_id, secret)
    if not rows:
        return Evidence(
            holds=False,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail=(
                "no wire-ledger rows for this run; cannot verify that the multimodal "
                "delivery was perceptible (inline_image / typed_block) rather than "
                "a non-perceptible resource_reference_replay"
            ),
        )
    for row in rows:
        delivery_mode = row.get("delivery_mode")
        if not isinstance(delivery_mode, str):
            continue
        if delivery_mode in _PERCEPTIBLE_DELIVERY_MODES:
            return None
    return Evidence(
        holds=False,
        provenance=Provenance.WORKSPACE_EFFECT,
        detail=(
            "no wire-ledger row for this run carries a perceptible delivery_mode "
            f"(inline_image / typed_block); saw delivery_mode values "
            f"{[row.get('delivery_mode') for row in rows]!r}. "
            "The multimodal fact graded at WIRE only when the agent's media call "
            "was actually delivered with inline bytes or a typed block, not as a "
            "resource_reference_replay the model could skip reading"
            + (
                f" (expected {expected_delivery_mode!r})"
                if expected_delivery_mode is not None
                else ""
            )
        ),
    )


def _check_perception_secret_token(
    output_file: Path,
    expected_perception_secret: str,
) -> Evidence | None:
    written_secret = _read_output_token(output_file, "PERCEPTION_SECRET")
    if written_secret is None:
        return Evidence(
            holds=False,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail=(
                "PERCEPTION_SECRET=<hex> token missing from smoke output; the agent "
                "did not write the pixel-only perception secret back to its output "
                "file, so the multimodal fact cannot be graded WIRE even when the "
                "ledger verifies -- the secret lives only in fixture pixels and a "
                "model that did not actually read the image cannot recover it"
            ),
        )
    if written_secret != expected_perception_secret:
        return Evidence(
            holds=False,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail=(
                f"PERCEPTION_SECRET={written_secret!r} does not match the fixture's "
                f"embedded perception secret {expected_perception_secret!r}; the "
                "agent wrote the wrong secret back (likely a guess rather than a "
                "read of the fixture pixels)"
            ),
        )
    return None


def _check_media_call_record(
    workspace_root: Path,
    run_id: str,
    secret: str | None,
) -> Evidence | None:
    read_media_call_present = wire_evidence_for(
        workspace_root,
        run_id,
        tool_name="read_media",
        secret=secret,
    ) or wire_evidence_for(
        workspace_root,
        run_id,
        tool_name="read_image",
        secret=secret,
    )
    if read_media_call_present:
        return None
    return Evidence(
        holds=False,
        provenance=Provenance.WORKSPACE_EFFECT,
        detail=(
            "no verified read_media / read_image tools/call record exists for this run; "
            "the receipt line is present in the output but cannot be attributed to a "
            "verified tool call"
        ),
    )


def _check_replay_record(
    workspace_root: Path,
    run_id: str,
    secret: str | None,
    server_uri: str,
) -> Evidence | None:
    replay_digest = params_digest(expected_replay_params(handle=server_uri))
    replay_record_present = wire_evidence_for(
        workspace_root,
        run_id,
        tool_name="read_media",
        secret=secret,
        params_digest=replay_digest,
    ) or wire_evidence_for(
        workspace_root,
        run_id,
        tool_name="read_image",
        secret=secret,
        params_digest=replay_digest,
    )
    if replay_record_present:
        return None
    return Evidence(
        holds=False,
        provenance=Provenance.WORKSPACE_EFFECT,
        detail=(
            "no verified replay call matches the digest of "
            f"{{'path': {server_uri!r}, 'format': 'inline'}}; "
            "a stub that dialed the media endpoint but discarded its response "
            "cannot grade WIRE"
        ),
    )


def _check_geometry_match(
    output_file: Path,
    width: int,
    height: int,
) -> Evidence | None:
    dimensions_token = _read_output_token(output_file, "DIMENSIONS")
    sha_token = _read_output_token(output_file, "MEDIA_SHA256")
    if dimensions_token is None or sha_token is None:
        return Evidence(
            holds=False,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail=(
                "DIMENSIONS or MEDIA_SHA256 token missing from smoke output; "
                "the receipts alone are not enough -- the agent must report the "
                "geometry and digest the server-supplied envelope gave it"
            ),
        )

    expected_dimensions = f"{width}x{height}"
    if dimensions_token != expected_dimensions:
        return Evidence(
            holds=False,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail=(
                f"DIMENSIONS={dimensions_token!r} does not match the fixture's "
                f"authoritative geometry {expected_dimensions!r}"
            ),
        )
    return None


def expected_fixture_sha256(width: int, height: int) -> str:
    return hashlib.sha256(build_smoke_fixture_png(width, height)).hexdigest()


__all__ = [
    "SMOKE_FIXTURE_RELNAME",
    "SMOKE_MEDIA_MAX_INLINE_BYTES",
    "build_smoke_fixture_png",
    "expected_fixture_sha256",
    "expected_replay_params",
    "extract_perception_secret_from_fixture",
    "generate_fixture_geometry",
    "grade_multimodal_evidence",
    "multimodal_prompt_requirements",
    "read_media_registry_for_fixture",
    "smoke_media_config_toml",
]
