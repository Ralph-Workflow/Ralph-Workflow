"""Multimodal smoke scenario: the fixture, prompt, and grader.

This module is the single owner of the multimodal smoke contract:
the deterministic fixture image, the response-derived answer an agent can
only produce by consuming what the media tool returned, the prompt
fragment, and the evidence grader. Keeping this in a dedicated module
(rather than growing the 2263-line :mod:`smoke_plumbing`) respects the
1000-line repo-structure rule (E1).

The contract decides *causal use*, not co-occurrence: a stub that dials
the endpoint and then writes a publicly-known answer must fail. Three
repository facts make that mechanically decidable:

- ``ralph/mcp/tools/workspace/_media_blocks.py`` 318-325 -- an image is
  delivered inline only when ``file_size <= max_inline_bytes``; otherwise
  the workspace media handler mints a manifest entry and returns a
  ``ResourceReferenceContent(uri="ralph://media/{artifact_id}")`` handle
  from ``new_artifact_id()``. That handle is minted server-side per call
  and exists nowhere before the response.
- ``ralph/mcp/server/_mcp_server.py`` 611 -- the wire ledger digests
  the tool-call **arguments** (``params=dict(arguments_value)``),
  HMAC-chained with a broker secret ``_subprocess_env`` strips from every
  agent child. A second call carrying the minted handle is therefore a
  signed, unforgeable record that the agent consumed the first response.
- ``ralph/mcp/tools/workspace/_media_handlers.py`` 110-124 --
  ``read_image(..., format="metadata")`` returns a server-computed
  ``width`` / ``height`` / ``sha256`` envelope, so the geometry the agent
  reports is a value the media response supplied.
"""

from __future__ import annotations

import hashlib
import secrets
import struct
import zlib
from typing import TYPE_CHECKING

from ralph.mcp.server._wire_ledger import (
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

#: Workspace-relative path of the deterministic PNG fixture the
#: multimodal smoke run materializes before the turns start. The agent
#: is told to read THIS path (not a server-minted handle), so the
#: server-side mint of the handle is the only honest way to satisfy the
#: replay hop. See ``multimodal_prompt_requirements``.
SMOKE_FIXTURE_RELNAME = "smoke-fixture.png"

#: The size cap pinned into the smoke harness's ``.agent/mcp.toml``.
#: Pinning ``max_inline_bytes`` low guarantees that **every** harness
#: identity (including the Claude-identity ones that would otherwise get
#: an inline image) takes the handle-minting path, so one uniform
#: assertion covers all six smoke commands. The replay hop still
#: delivers the inline image block on those identities
#: (``_replay_from_manifest_entry`` applies no size test), so real
#: inline delivery is still exercised end-to-end.
SMOKE_MEDIA_MAX_INLINE_BYTES = 1024

#: Lower bound the fixture must exceed so the test reuses the
#: ``SMOKE_MEDIA_MAX_INLINE_BYTES`` handle-mint path on every harness
#: identity (inline-image eligible files AT-OR-BELOW this size would
#: short-circuit to an inline delivery, defeating the test). The PNG
#: builder below paints enough rows to keep the file comfortably above
#: this bound for any reasonable geometry.
_MIN_FIXTURE_SIZE_BYTES = 1200


def build_smoke_fixture_png(width: int, height: int) -> bytes:
    """Build a stdlib-only PNG payload reporting ``width`` x ``height``.

    No Pillow dependency. The format is RFC-2083 PNG:

    1. PNG signature (8 bytes).
    2. IHDR chunk carrying ``width`` (4B big-endian), ``height`` (4B
       big-endian), bit depth, color type, compression, filter, and
       interlace.
    3. IDAT chunk carrying the zlib-compressed scanlines. Each scanline
       is prefixed with a single-byte filter selector (``0`` = None)
       and carries the row's pixels as RGB triples. To keep the
       fixture larger than :data:`SMOKE_MEDIA_MAX_INLINE_BYTES` across
       every realistic geometry the row content is interleaved with a
       per-run 32-byte random salt that defeats zlib's compression.
    4. A tEXt ancillary chunk carrying that same salt. Spec readers
       ignore it; the salt reliably inflates the fixture above the
       inline-cap threshold for every geometry so the handle-mint path
       is uniform across all six smoke commands.
    5. IEND chunk.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"width and height must be positive, got {width=} {height=}")
    png_signature = b"\x89PNG\r\n\x1a\n"

    def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
        """Build one PNG chunk: length + type + payload + CRC32."""
        length = struct.pack(">I", len(payload))
        crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        return length + chunk_type + payload + struct.pack(">I", crc)

    # IHDR payload: width (4B) + height (4B) + bit_depth (1B) +
    # color_type (1B) + compression (1B) + filter (1B) + interlace (1B).
    # RGB = color_type 2, 8-bit depth, deflate compression, no filter,
    # no interlace.
    ihdr_payload = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_chunk = _chunk(b"IHDR", ihdr_payload)

    salt = hashlib.sha256(f"{width},{height}".encode()).digest()
    raw_scanlines = bytearray()
    for row_index in range(height):
        raw_scanlines.append(0)  # filter byte: None
        for column_index in range(width * 3):
            # Salt interleaved with row/column makes per-row content
            # non-redundant so the zlib stream stays above the inline
            # cap even at the smallest geometry.
            raw_scanlines.append((row_index ^ column_index ^ salt[column_index % len(salt)]) & 0xFF)
    idat_payload = zlib.compress(bytes(raw_scanlines), level=6)
    idat_chunk = _chunk(b"IDAT", idat_payload)

    text_payload = b"Comment\x00" + salt + b"smoke-fixture-salt"
    text_chunk = _chunk(b"tEXt", text_payload)

    iend_chunk = _chunk(b"IEND", b"")

    return png_signature + ihdr_chunk + idat_chunk + text_chunk + iend_chunk


def generate_fixture_geometry(
    *,
    rng: Callable[[int], int] | None = None,
) -> tuple[int, int]:
    """Pick a per-run PNG geometry so the expected answer exists nowhere else.

    The caller picks ``width`` / ``height`` per run with ``secrets.randbelow``
    so the expected answer exists nowhere in the repository, the prompt,
    or a prior run. The bounds are sized so the resulting PNG reliably
    exceeds :data:`SMOKE_MEDIA_MAX_INLINE_BYTES` on every realistic geometry:
    the IDAT stream is zlib-compressed, so even the smallest geometry
    produces a fixture comfortably above the 1024-byte inline cap. The
    deleted fixed geometry ``137x89`` is excluded exactly so a regression
    that re-introduced it would visibly disagree with the runtime RNG.
    """
    picker = rng if rng is not None else secrets.randbelow
    width = picker(40) + 24  # 24..63
    height = picker(40) + 24  # 24..63
    return width, height


def smoke_media_config_toml() -> str:
    """Return the ``[media] max_inline_bytes = ...`` fragment for the run's mcp.toml.

    ``max_inline_bytes`` stays a genuine tunable; we pin it low only to
    guarantee every harness identity takes the handle-mint path so the
    replay-hop assertion can be uniform across the six transports.
    """
    return (
        "[media]\n"
        f"max_inline_bytes = {SMOKE_MEDIA_MAX_INLINE_BYTES}\n"
    )


def multimodal_prompt_requirements(fixture_relpath: str) -> str:
    """Extra prompt bullets appended to the smoke prompt when --multimodal is set.

    The bullets tell the agent to:

    1. Read the on-disk fixture with the media tool the transport exposes.
    2. Take the ``ralph://media/{artifact_id}`` the first response
       returned and replay it with a second ``read_media`` call.
    3. Ask ``read_image`` for the metadata envelope to recover the
       authoritative geometry and sha256.
    4. Write exactly ``MEDIA_RECEIPT=<handle>``, ``DIMENSIONS=<width>x<height>``
       and ``MEDIA_SHA256=<sha256>`` into the existing output file.
    """
    return (
        "Multimodal contract (DO NOT skip any of these):\n"
        f"- Read the on-disk fixture `{fixture_relpath}` with the media tool this "
        "transport exposes (for example, `read_media` or `read_image` with "
        "`path=\"" + fixture_relpath + "\"`).\n"
        "- The first response will mint a fresh `ralph://media/{artifact_id}` "
        "handle. You MUST take that handle exactly as returned and call "
        "`read_media` (or `read_image`) AGAIN with `path=<that handle>` as a "
        "second, fresh tool call. The replay hop's `params_digest` is the "
        "graded proof you consumed the first response -- skipping it fails "
        "the run, even if a receipt line is written.\n"
        "- Then call `read_image` with `format=\"metadata\"` against the same "
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
) -> dict[str, str] | None:
    """Return the registry entry the server persisted for ``fixture_relpath``.

    Reads ``.agent/tmp/media_registry.json`` -- the workspace-scoped,
    HMAC-less durable index the workspace media handler writes whenever
    it mints a ``ralph://media/{artifact_id}`` handle. The grader uses
    this entry's ``uri`` (``source_path`` / ``artifact_id``-keyed) as
    the only honest match for ``MEDIA_RECEIPT`` in the smoke output.

    Returns ``None`` when the registry is missing or the fixture has not
    been processed yet (a run that never made the media call).
    """
    registry_path = workspace_root / media_registry_path()
    if not registry_path.exists():
        return None
    import json  # local import: registry read is a single-shot per call

    data: object = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    artifacts_obj = data.get("artifacts")
    if not isinstance(artifacts_obj, list):
        return None
    for entry in artifacts_obj:
        if not isinstance(entry, dict):
            continue
        entry_source: object = entry.get("source_path")
        if entry_source == fixture_relpath:
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
    """Return the canonical params the agent's replay call carries.

    The grader matches the server's wire-ledger record against the digest
    of this dict. ``read_media`` accepts an optional ``format`` argument;
    including the same value the canonical first-replay call uses keeps
    the replay-hop assertion uniform regardless of whether the agent
    passed ``"inline"`` explicitly or relied on the default.
    """
    return {"path": handle, "format": "inline"}


def _read_output_token(output_file: Path, key: str) -> str | None:
    """Return the value carried by a ``KEY=value`` line, or ``None``.

    Token lines are appended to the smoke output file by the agent. We
    match a strict prefix (``KEY=`` followed by a single value) at line
    start; lines with leading whitespace, code-fence markers, or
    multi-token commentary do NOT count -- the agent must put the exact
    one-line token we asked for.
    """
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
    """Validate the MEDIA_RECEIPT line itself; returns a downgrade ``Evidence`` or ``None``.

    The token is either the empty string (no line), a real
    ``ralph://media/{uuid}`` handle, or a forgery. The check is
    split out so the main ``grade_multimodal_evidence`` body
    stays well below the audit's PLR0912 branch-count cap.
    """
    if not receipt_token:
        return absent(
            "MEDIA_RECEIPT=<handle> line was not written to the smoke output file"
        )
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
    """Confirm the server-persisted receipt matches the agent's token.

    Returns ``None`` on success (the returned string is the
    server-minted URI to use in the next stage), or a downgrade
    ``Evidence`` on failure.
    """
    registry_entry = read_media_registry_for_fixture(workspace_root, fixture_relpath)
    if registry_entry is None:
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
    """Confirm the wire-ledger chain verifies; returns ``None`` on success."""
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


def grade_multimodal_evidence(  # noqa: PLR0911  # 4-condition contract: 7 returns (absent, broker, media call, replay, geometry-absent, geometry-mismatch, ok)
    workspace_root: Path,
    run_id: str,
    *,
    output_file: Path,
    fixture_relpath: str,
    fixture_size: tuple[int, int],
    secret: str | None,
) -> Evidence:
    """Grade the multimodal "agent actually used the media endpoint" fact.

    Returns ``Evidence`` carrying one of three provenances:

    - ``WIRE`` when all four contract conditions hold (verified
      ``read_media`` call, server-persisted receipt equals the agent's
      written token, verified replay-digest call matching the server-
      minted handle, geometry and sha256 match what the agent wrote).
    - ``WORKSPACE_EFFECT`` when the model self-reported plausible
      tokens but the ledger is missing the underlying verified calls
      (e.g. agent fabricated the receipt, or the smoke stub dialed the
      endpoint but discarded the response). This is the same downgrade
      the existing artifact-submission grader uses for "I see the file
      but the wire did not carry it": durable but not WIRE-grade.
    - ``ABSENT`` (via :func:`absent`) when no token line exists.

    The body delegates each contract condition to a dedicated helper
    so the function stays well below the audit's PLR0912
    branch-count cap (14); each helper itself stays well below
    PLR0911's 6-return cap.
    """
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

    replay_check = _check_replay_record(
        workspace_root, run_id, secret, server_uri
    )
    if replay_check is not None:
        return replay_check

    geometry = _check_geometry_match(
        output_file, width, height
    )
    if geometry is not None:
        return geometry

    return Evidence(
        holds=True,
        provenance=Provenance.WIRE,
        detail=(
            "verified read_media + verified replay-hop call + server-persisted "
            "MEDIA_RECEIPT equal to MEDIA_SHA256-grade geometry from the fixture"
        ),
    )


def _check_media_call_record(
    workspace_root: Path,
    run_id: str,
    secret: str | None,
) -> Evidence | None:
    """Confirm a verified read_media / read_image record exists for the run."""
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
    """Confirm a verified replay call carrying ``server_uri`` exists for the run."""
    replay_digest = params_digest(expected_replay_params(handle=server_uri))
    replay_record_present = wire_evidence_for(
        workspace_root,
        run_id,
        tool_name="read_media",
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
    """Confirm ``DIMENSIONS`` matches the fixture's authoritative geometry and the sha token is present."""
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
    """Return the canonical sha256 of a fixture with ``width`` x ``height``.

    Exposed so smoke tests that drive a deterministic ``width`` /
    ``height`` (e.g. for fast parameter sweeps) can pre-compute the
    expected digest without rebuilding the fixture bytes.
    """
    return hashlib.sha256(build_smoke_fixture_png(width, height)).hexdigest()


__all__ = [
    "SMOKE_FIXTURE_RELNAME",
    "SMOKE_MEDIA_MAX_INLINE_BYTES",
    "build_smoke_fixture_png",
    "expected_fixture_sha256",
    "expected_replay_params",
    "generate_fixture_geometry",
    "grade_multimodal_evidence",
    "multimodal_prompt_requirements",
    "read_media_registry_for_fixture",
    "smoke_media_config_toml",
]
