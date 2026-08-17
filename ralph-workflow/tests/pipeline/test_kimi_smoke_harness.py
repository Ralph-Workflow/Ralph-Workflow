"""Kimi smoke-harness layout resolution (PA-003).

``resolve_smoke_harness_spec`` must resolve the bare ``kimi`` agent and
the ``kimi/<model>`` dynamic alias to the kimi harness layout
(``tmp/interactive-kimi-smoke``), with a sanitized model suffix that
keeps two concurrent model smoke runs from colliding on run_id /
output_file paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline.plumbing.smoke_plumbing import (
    _KIMI_SMOKE_OUTPUT_FILE,
    _KIMI_SMOKE_RELATIVE_DIR,
    _KIMI_SMOKE_RUN_ID,
    resolve_smoke_harness_spec,
)

pytestmark = pytest.mark.smoke


def test_bare_kimi_uses_base_harness_layout() -> None:
    spec = resolve_smoke_harness_spec("kimi")

    assert spec.agent_name == "kimi"
    assert spec.relative_dir == Path("tmp/interactive-kimi-smoke")
    assert spec.output_file == Path("tmp/interactive-kimi-smoke/todo-list.js")
    assert spec.run_id == "interactive-kimi-smoke"
    assert spec.relative_dir == _KIMI_SMOKE_RELATIVE_DIR
    assert spec.output_file == _KIMI_SMOKE_OUTPUT_FILE
    assert spec.run_id == _KIMI_SMOKE_RUN_ID


def test_kimi_model_alias_sanitizes_suffix_into_run_id_and_dir() -> None:
    spec = resolve_smoke_harness_spec("kimi/kimi-code/k3-256k")

    assert spec.agent_name == "kimi/kimi-code/k3-256k"
    assert spec.run_id.startswith("interactive-kimi-smoke-")
    assert spec.run_id == "interactive-kimi-smoke-kimi-code-k3-256k"
    # Slashes in the model id sanitize to dashes; the output file stays
    # under the kimi smoke root in its own model-scoped sub-directory.
    assert str(spec.output_file).startswith("tmp/interactive-kimi-smoke/")
    assert spec.output_file.name == "todo-list.js"


def test_two_kimi_model_aliases_do_not_collide() -> None:
    spec_a = resolve_smoke_harness_spec("kimi/kimi-code/k3-256k")
    spec_b = resolve_smoke_harness_spec("kimi/kimi-for-coding")

    assert spec_a.run_id != spec_b.run_id
    assert spec_a.output_file != spec_b.output_file
    assert spec_a.relative_dir != spec_b.relative_dir


def test_bare_and_aliased_kimi_run_ids_differ() -> None:
    bare = resolve_smoke_harness_spec("kimi")
    aliased = resolve_smoke_harness_spec("kimi/kimi-for-coding")

    assert bare.run_id != aliased.run_id
    assert bare.run_id == "interactive-kimi-smoke"
    assert aliased.run_id == "interactive-kimi-smoke-kimi-for-coding"


def test_kimi_smoke_layout_does_not_collide_with_other_transports() -> None:
    kimi = resolve_smoke_harness_spec("kimi")
    pi = resolve_smoke_harness_spec("pi")
    cursor = resolve_smoke_harness_spec("cursor")

    assert kimi.relative_dir != pi.relative_dir
    assert kimi.relative_dir != cursor.relative_dir
    assert kimi.run_id != pi.run_id
    assert kimi.run_id != cursor.run_id


def test_unknown_agent_still_raises() -> None:
    with pytest.raises(ValueError, match="No smoke harness spec defined"):
        resolve_smoke_harness_spec("not-an-agent")
