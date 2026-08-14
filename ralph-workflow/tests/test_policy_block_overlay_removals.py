"""Block-authored overlays may rename and remove blocks.

A workspace `pipeline.toml` is merged over the bundled defaults. Phases the
overlay omits are treated as removed, but blocks were deep-unioned, so every
bundled block survived into a renamed graph and compiled to phases nothing
reaches. The runtime load path then rejected the workflow, while
`--check-config` — which does not merge — reported it as fine.
"""

from __future__ import annotations

from pathlib import Path

from ralph.policy.loader import load_policy, merge_pipeline_defaults

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def test_overlay_blocks_replace_rather_than_union_with_defaults() -> None:
    defaults = {"blocks": {"planning": {"kind": "individual"}, "legacy": {"kind": "group"}}}
    overrides = {"blocks": {"planning": {"kind": "individual"}}}

    merged = merge_pipeline_defaults(defaults, overrides)

    assert set(merged["blocks"]) == {"planning"}


def test_overlay_block_still_inherits_fields_it_does_not_restate() -> None:
    defaults = {"blocks": {"planning": {"kind": "individual", "phase_name": "planning"}}}
    overrides = {"blocks": {"planning": {"kind": "individual"}}}

    merged = merge_pipeline_defaults(defaults, overrides)

    assert merged["blocks"]["planning"]["phase_name"] == "planning"


def test_a_deleted_optional_section_stays_deleted(tmp_path: Path) -> None:
    """An overlay that drops `[cycle_timebox]` must not have it merged back in."""
    bundled = (_DEFAULTS_DIR / "pipeline.toml").read_text(encoding="utf-8")
    kept: list[str] = []
    skipping = False
    for line in bundled.splitlines():
        if line.strip().startswith("[") and not line.strip().startswith("[["):
            skipping = line.strip() == "[cycle_timebox]"
        if not skipping:
            kept.append(line)
    (tmp_path / "pipeline.toml").write_text("\n".join(kept), encoding="utf-8")
    for name in ("agents.toml", "artifacts.toml"):
        (tmp_path / name).write_text(
            (_DEFAULTS_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )

    assert load_policy(tmp_path).pipeline.cycle_timebox is None


def test_renamed_block_does_not_drag_the_bundled_one_along() -> None:
    defaults = {
        "blocks": {
            "development": {"kind": "individual", "phase_name": "development"},
            "complete": {"kind": "individual", "phase_name": "complete"},
        }
    }
    overrides = {
        "blocks": {
            "build": {"kind": "individual", "phase_name": "build"},
            "complete": {"kind": "individual", "phase_name": "complete"},
        }
    }

    merged = merge_pipeline_defaults(defaults, overrides)

    assert set(merged["blocks"]) == {"build", "complete"}
