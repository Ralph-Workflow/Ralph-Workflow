from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CUSTOM_CSS_PATH = REPO_ROOT / "docs" / "sphinx" / "_static" / "custom.css"


def _custom_css() -> str:
    return CUSTOM_CSS_PATH.read_text(encoding="utf-8")


def test_light_code_block_background_does_not_treat_auto_mode_as_light() -> None:
    css = _custom_css()

    assert '[data-theme="light"] div[class*="highlight"]' in css
    assert "@media (prefers-color-scheme: light)" in css
    assert 'body:not([data-theme]) div[class*="highlight"]' in css
    assert 'body:not([data-theme="dark"]) div[class*="highlight"]' not in css


def test_light_card_hover_does_not_treat_auto_mode_as_light() -> None:
    css = _custom_css()

    assert '[data-theme="light"] .sd-card:hover' in css
    assert "@media (prefers-color-scheme: light)" in css
    assert "body:not([data-theme]) .sd-card:hover" in css
    assert 'body:not([data-theme="dark"]) .sd-card:hover' not in css
