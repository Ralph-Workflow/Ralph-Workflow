"""Mechanical disposition oracle for ``docs/ralph-workflow-policy/policy-portfolio.toml``.

This oracle enforces four structural rules declared by the testing policy:

1. Every ``REMOVE``/``MERGE``/``REPLACE`` control carries an explicit
   ``category-[1-4]`` token somewhere in
   ``fault_sensitivity``/``layer``/``evidence``/``lifecycle``.
2. Every ``MERGE``/``REPLACE`` control names the surviving or replacement test
   in ``evidence`` or ``lifecycle``.
3. Every ``human``- or ``triggered``-lane control records an evidence
   location: a dated review note, a named script, or a named receipt.
4. No name from a ``removed-tests:`` marker (or the floor set of test names
   predating this oracle) appears in any documentation root.

The oracle uses ``tomllib`` + ``pathlib`` only — no private ``ralph`` names,
no source reading, no I/O outside the documentation roots. Reads stay
inside the 1 s per-test cap because the sweep is bounded by the size of
the docs roots.

See: ``docs/ralph-workflow-policy/policy-portfolio.toml`` and the S-7 step
of ``.agent/PLAN.md``.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from pathlib import Path

from tests.doc_roots import PACKAGE_ROOT, REPOSITORY_ROOT

PORTFOLIO_PATH = REPOSITORY_ROOT / "docs" / "ralph-workflow-policy" / "policy-portfolio.toml"

# Floor set of test names removed before this oracle existed. Their
# appearance in any documentation root is an automatic fail (a stale
# citation).
FLOOR_REMOVED_NAMES: frozenset[str] = frozenset(
    {
        "test_live_search_returns_results",
        "test_live_search_returns_results_when_searxng_url_configured",
        "test_every_claimed_test_fails_under_its_regression_probe",
    }
)

# Documentation roots swept for stale removed-name citations. Excludes the
# portfolio itself (which legitimately records the names) and this oracle
# (whose floor-set literal would otherwise self-match).
DOCS_ROOTS: tuple[Path, ...] = (
    REPOSITORY_ROOT / "docs",
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "docs" / "README.md",
    PACKAGE_ROOT / "docs",
    PACKAGE_ROOT / "README.md",
    PACKAGE_ROOT / "docs" / "README.md",
    PACKAGE_ROOT / "CONTRIBUTING.md",
    PACKAGE_ROOT / "pytest.ini",
)

_SCAN_SUFFIXES: frozenset[str] = frozenset(
    {".md", ".rst", ".txt", ".ini", ".toml", ".yaml", ".yml", ".py"}
)

_CATEGORY_RE = re.compile(r"category-[1-4]")
_DATE_RE = re.compile(r"\b202\d-\d{2}-\d{2}\b")
_SCRIPT_RE = re.compile(r"\b\w[\w/-]*\.sh\b|\bscripts/\S+")
_RECEIPT_RE = re.compile(
    r"\b(receipt|stdout|stderr|run-[a-z0-9_-]+|commit[: ][a-f0-9]{7,})\b"
)
_TEST_NAME_RE = re.compile(r"\btest_[a-z0-9_]+\b")
_DISPOSITIONS_REQUIRING_CATEGORY: frozenset[str] = frozenset(
    {"REMOVE", "MERGE", "REPLACE"}
)
_DISPOSITIONS_REQUIRING_SURVIVOR: frozenset[str] = frozenset({"MERGE", "REPLACE"})
_EVIDENCE_LOCATION_LANES: frozenset[str] = frozenset({"human", "triggered"})


def _load_portfolio() -> dict[str, object]:
    with PORTFOLIO_PATH.open("rb") as fh:
        return tomllib.load(fh)


def _iter_controls(portfolio: dict[str, object]) -> Iterable[dict[str, object]]:
    controls = portfolio.get("controls", [])
    if not isinstance(controls, list):
        return
    for entry in controls:
        if isinstance(entry, dict):
            yield entry


def _control_text_blob(entry: dict[str, object], fields: tuple[str, ...]) -> str:
    return " ".join(str(entry.get(field, "")) for field in fields)


def _scan_paths_for_removed_names() -> list[tuple[Path, str]]:
    """Return ``(path, name)`` hits for any removed-test name found in docs roots."""
    portfolio = _load_portfolio()
    removed_names: set[str] = set(FLOOR_REMOVED_NAMES)
    for entry in _iter_controls(portfolio):
        marker = entry.get("removed-tests")
        if isinstance(marker, str):
            for raw in marker.split(","):
                cleaned = raw.strip()
                if cleaned:
                    removed_names.add(cleaned)

    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(name) for name in sorted(removed_names)) + r")\b"
    )

    paths: list[Path] = []
    for root in DOCS_ROOTS:
        if root.is_file():
            paths.append(root)
        elif root.is_dir():
            paths.extend(p for p in root.rglob("*") if p.is_file())

    hits: list[tuple[Path, str]] = []
    for path in paths:
        if path == PORTFOLIO_PATH:
            continue
        if path.suffix not in _SCAN_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in pattern.finditer(content):
            hits.append((path, match.group(0)))
    return hits


def test_remove_merge_replace_controls_carry_a_category_token() -> None:
    """Every REMOVE/MERGE/REPLACE control names a category-1..4 token."""
    portfolio = _load_portfolio()
    fields = ("fault_sensitivity", "layer", "evidence", "lifecycle")
    missing: list[str] = []
    for entry in _iter_controls(portfolio):
        disposition = entry.get("disposition")
        if disposition not in _DISPOSITIONS_REQUIRING_CATEGORY:
            continue
        if not _CATEGORY_RE.search(_control_text_blob(entry, fields)):
            missing.append(str(entry.get("id", "<no id>")))
    assert not missing, (
        "REMOVE/MERGE/REPLACE controls missing a category-[1-4] token: "
        + ", ".join(missing)
    )


def test_merge_replace_controls_name_a_surviving_test() -> None:
    """Every MERGE/REPLACE control records the surviving/replacement test name."""
    portfolio = _load_portfolio()
    fields = ("evidence", "lifecycle")
    missing: list[str] = []
    for entry in _iter_controls(portfolio):
        disposition = entry.get("disposition")
        if disposition not in _DISPOSITIONS_REQUIRING_SURVIVOR:
            continue
        if not _TEST_NAME_RE.search(_control_text_blob(entry, fields)):
            missing.append(str(entry.get("id", "<no id>")))
    assert not missing, (
        "MERGE/REPLACE controls missing a named surviving/replacement test: "
        + ", ".join(missing)
    )


def test_human_or_triggered_lane_controls_record_evidence_location() -> None:
    """Every human/triggered-lane control records an evidence location."""
    portfolio = _load_portfolio()
    fields = ("evidence", "lifecycle")
    missing: list[str] = []
    for entry in _iter_controls(portfolio):
        lane = entry.get("lane")
        if lane not in _EVIDENCE_LOCATION_LANES:
            continue
        blob = _control_text_blob(entry, fields)
        has_dated = bool(_DATE_RE.search(blob))
        has_script = bool(_SCRIPT_RE.search(blob))
        has_receipt = bool(_RECEIPT_RE.search(blob))
        if not (has_dated or has_script or has_receipt):
            missing.append(str(entry.get("id", "<no id>")))
    assert not missing, (
        "human/triggered-lane controls missing an evidence location "
        "(dated review note, named script, or receipt): "
        + ", ".join(missing)
    )


def test_no_removed_test_names_in_documentation_roots() -> None:
    """No removed test name (floor set + removed-tests markers) appears in docs roots."""
    hits = _scan_paths_for_removed_names()
    rendered = "\n".join(f"{path}: {name}" for path, name in hits[:10])
    assert not hits, (
        "Removed test names found in documentation roots:\n" + rendered
    )
