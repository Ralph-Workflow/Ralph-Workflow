from __future__ import annotations

import re
from pathlib import Path

from ralph.prompts.template_registry import load_partial_templates, packaged_template_root

_MAX_ADDED_LINES = 2
_MAX_CUE_LINE_CHARACTERS = 160
_MAX_ADDED_BYTES = _MAX_ADDED_LINES * _MAX_CUE_LINE_CHARACTERS
_POST_TRIM_BASELINES = {
    "shared/_developer_iteration_guidance.j2": (59, 5886),
    "shared/_verification_commitments.j2": (12, 728),
}
_SIZE_RULE = (
    "each post-trim fragment has two cue lines of headroom, and each installed or "
    "absent cue branch stays on one line of at most 160 characters"
)
_CUE_BLOCK: re.Pattern[str] = re.compile(
    r"\{% if SKILL_INSTALLED_[^%]+%\}\n([^\n]+)\n"
    r"\{% else %\}\n([^\n]+)\n\{% endif %\}"
)


def _packaged_source(relative_path: str) -> str:
    partials = load_partial_templates([packaged_template_root()])
    stem = Path(relative_path).with_suffix("").as_posix()
    for key, text in partials.items():
        if key.endswith(relative_path) or key.endswith(stem):
            return text
    raise AssertionError(f"packaged template not found: {relative_path}")


def test_packaged_prompt_fragments_keep_skill_cues_within_growth_budget() -> None:
    for relative_path, (baseline_lines, baseline_bytes) in _POST_TRIM_BASELINES.items():
        source = _packaged_source(relative_path)
        cue_branches: list[tuple[str, str]] = _CUE_BLOCK.findall(source)

        assert len(source.splitlines()) <= baseline_lines + _MAX_ADDED_LINES, _SIZE_RULE
        assert len(source.encode("utf-8")) <= baseline_bytes + _MAX_ADDED_BYTES, _SIZE_RULE
        assert len(cue_branches) == source.count("{% if SKILL_INSTALLED_"), _SIZE_RULE
        assert all(
            len(line) <= _MAX_CUE_LINE_CHARACTERS for branches in cue_branches for line in branches
        ), _SIZE_RULE
