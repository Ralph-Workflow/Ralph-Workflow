#!/usr/bin/env python3
"""Star Conversion Agent — bridges PyPI downloads → Codeberg stars gap.

Problem: 1,329 PyPI downloads/month (5/day) → 0 Codeberg stars across 9+ samples.
Users install and use Ralph Workflow but never star the primary repo.

This agent:
1. Reads adoption_metrics_latest.md daily
2. Tracks downloads-to-stars conversion ratio
3. Verifies that the in-pipeline star CTA is functional (runner.py periodic CTA)
4. When the gap is chronic (7+ days of 0-star delta with >3 downloads/day):
   - Recommends increasing CTA frequency (20% → 50%)
   - Recommends adding CLI "ralph star" command
   - Emits strengthening recommendation to shared_findings

Structural mandate: Per MARKETING_WORKFLOW_PRINCIPLES.md Principle 10, when adoption
is flat across 3+ audits, the system MUST create new agents — this agent is that.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path("/home/mistlight/.openclaw/workspace")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AGENTS_DIR = ROOT / "agents/marketing"
LOG_DIR = AGENTS_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

ADOPTION_MD = LOG_DIR / "adoption_metrics_latest.md"
ADOPTION_JSON = LOG_DIR / "adoption_metrics_latest.json"
STAR_CONVERSION_JSON = LOG_DIR / "star_conversion_latest.json"
SHARED_FINDINGS_DIR = ROOT / "drafts"
STAR_CONVERSION_FINDING = SHARED_FINDINGS_DIR / "star_conversion_finding.md"

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_DOWNLOADS_PER_DAY_FOR_CONCERN = 3          # Below this, not enough users to convert
ZERO_STAR_DAYS_FOR_CHRONIC = 7                  # 7+ consecutive days of flat stars = chronic
CTA_WEAKNESS_DAYS_FOR_ESCALATION = 14           # 14 days = escalate to structural recommendation
CTA_STRENGTHENING_THRESHOLD = 3                 # After 3 days of zero conversion, recommend strengthening
CHRONIC_RECOMMENDATION_INTERVAL_DAYS = 3        # Don't re-emit the same recommendation within 3 days


def _parse_iso_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _read_adoption_json() -> dict[str, Any] | None:
    if not ADOPTION_JSON.exists():
        return None
    try:
        return json.loads(ADOPTION_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_adoption_md() -> dict[str, Any]:
    """Parse adoption_metrics_latest.md into structured data."""
    result: dict[str, Any] = {
        "codeberg": {"stars": 0, "watchers": 0, "forks": 0, "window_samples": 0},
        "pypi": {"downloads_month": 0, "downloads_week": 0, "downloads_day": 0},
        "github": {"stars": 0, "watchers": 0, "forks": 0, "window_samples": 0},
        "timestamp": None,
    }

    if not ADOPTION_MD.exists():
        return result

    text = ADOPTION_MD.read_text(encoding="utf-8")

    # Parse timestamp
    for line in text.splitlines():
        if "Timestamp:" in line or "timestamp:" in line:
            ts_str = line.split(":", 1)[-1].strip()
            result["timestamp"] = ts_str
            break

    # Parse Codeberg
    in_codeberg = False
    for line in text.splitlines():
        if "Codeberg" in line and ("primary" in line.lower() or "##" in line):
            in_codeberg = True
            continue
        if in_codeberg:
            if line.startswith("##") or line.startswith("#"):
                in_codeberg = False
                continue
            if "Stars:" in line:
                result["codeberg"]["stars"] = int(line.split(":", 1)[-1].strip().split()[0])
            elif "Watchers:" in line:
                result["codeberg"]["watchers"] = int(line.split(":", 1)[-1].strip().split()[0])
            elif "Forks:" in line:
                result["codeberg"]["forks"] = int(line.split(":", 1)[-1].strip().split()[0])
            elif "Window samples:" in line or "samples:" in line:
                try:
                    result["codeberg"]["window_samples"] = int(line.split(":", 1)[-1].strip().split()[0])
                except (ValueError, IndexError):
                    pass

    # Parse PyPI
    in_pypi = False
    for line in text.splitlines():
        if "PyPI" in line and ("package" in line.lower() or "##" in line):
            in_pypi = True
            continue
        if in_pypi:
            if line.startswith("##") or line.startswith("#"):
                in_pypi = False
                continue
            if "month" in line.lower() and ":" in line:
                try:
                    result["pypi"]["downloads_month"] = int(line.split(":", 1)[-1].strip().split()[0])
                except (ValueError, IndexError):
                    pass
            elif "week" in line.lower() and ":" in line:
                try:
                    result["pypi"]["downloads_week"] = int(line.split(":", 1)[-1].strip().split()[0])
                except (ValueError, IndexError):
                    pass
            elif "day" in line.lower() and ":" in line:
                try:
                    result["pypi"]["downloads_day"] = int(line.split(":", 1)[-1].strip().split()[0])
                except (ValueError, IndexError):
                    pass

    # Parse GitHub
    in_github = False
    for line in text.splitlines():
        if "GitHub" in line and ("mirror" in line.lower() or "##" in line):
            in_github = True
            continue
        if in_github:
            if line.startswith("##") or line.startswith("#"):
                in_github = False
                continue
            if "Stars:" in line:
                result["github"]["stars"] = int(line.split(":", 1)[-1].strip().split()[0])
            elif "Watchers:" in line:
                result["github"]["watchers"] = int(line.split(":", 1)[-1].strip().split()[0])
            elif "Forks:" in line:
                result["github"]["forks"] = int(line.split(":", 1)[-1].strip().split()[0])

    return result


def _load_previous_conversion() -> dict[str, Any]:
    if not STAR_CONVERSION_JSON.exists():
        return {
            "first_sample_at": None,
            "samples": [],
            "chronic_started_at": None,
            "last_recommendation_at": None,
            "recommendation_count": 0,
        }
    try:
        return json.loads(STAR_CONVERSION_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "first_sample_at": None,
            "samples": [],
            "chronic_started_at": None,
            "last_recommendation_at": None,
            "recommendation_count": 0,
        }


def _save_conversion(data: dict[str, Any]) -> None:
    STAR_CONVERSION_JSON.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def _verify_runner_cta(now: datetime) -> dict[str, Any]:
    """Verify that the in-pipeline star CTA actually works.

    Checks for CODEBERG_STAR_CTA in runner.py and ralph/onboarding module.
    """
    result: dict[str, Any] = {
        "runner_cta_exists": False,
        "onboarding_import_works": False,
        "cta_frequency": "50%",  # Raised from 20% in audit #22 (2026-06-03)
        "cta_issues": [],
    }

    # Canonical repo (not stale Ralph-Site vendor submodule — git #1c366a2ab)
    runner_path = Path("/home/mistlight/Ralph-Workflow/ralph-workflow/ralph/pipeline/runner.py")
    if runner_path.exists():
        runner_text = runner_path.read_text(encoding="utf-8")
        if "CODEBERG_STAR_CTA" in runner_text:
            result["runner_cta_exists"] = True
        if "% 2) == 0" in runner_text:
            # The hash-based 50% frequency check (raised from 20% audit #22)
            result["cta_frequency"] = "50% (hash-based, 1-in-2)"
        elif "% 5) == 0" in runner_text:
            result["cta_frequency"] = "20% (hash-based, 1-in-5)"
            result["cta_issues"].append("⚠️ CTA frequency still at 20% — should be 50% per audit #22")
        elif "hash(" in runner_text:
            result["cta_issues"].append("No frequency gate found — CTA may not fire at expected rate")

    # Check if the onboarding module actually exists with CODEBERG_STAR_CTA
    # Canonical repo (not stale Ralph-Site vendor submodule — git #1c366a2ab)
    onboarding_paths = [
        Path("/home/mistlight/Ralph-Workflow/ralph-workflow/ralph/onboarding.py"),
        ROOT / "Ralph-Site/vendor/Ralph-Workflow/ralph-workflow/ralph/onboarding.py",
    ]
    for p in onboarding_paths:
        if p.exists():
            text = p.read_text(encoding="utf-8")
            if "CODEBERG_STAR_CTA" in text:
                result["onboarding_import_works"] = True
                # Extract the actual CTA message
                for line in text.splitlines():
                    if "CODEBERG_STAR_CTA" in line and "=" in line:
                        cta_text = line.split("=", 1)[-1].strip().strip('"').strip("'")
                        result["cta_text"] = cta_text[:200]
                        break
                break

    if not result["runner_cta_exists"]:
        result["cta_issues"].append("CODEBERG_STAR_CTA not found in runner.py — CTA may be missing entirely")
    if not result["onboarding_import_works"]:
        result["cta_issues"].append("ralph.onboarding module not found or CODEBERG_STAR_CTA not defined — import will fail at runtime")

    result["checked_at"] = now.isoformat()
    return result


def _compute_conversion_status(
    current: dict[str, Any],
    previous: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Compute the conversion gap and determine action level."""
    downloads_day = current.get("pypi", {}).get("downloads_day", 0)
    downloads_month = current.get("pypi", {}).get("downloads_month", 0)
    codeberg_stars = current.get("codeberg", {}).get("stars", 0)

    sample = {
        "at": now.isoformat(),
        "downloads_day": downloads_day,
        "downloads_month": downloads_month,
        "codeberg_stars": codeberg_stars,
        "github_stars": current.get("github", {}).get("stars", 0),
    }

    samples = previous.get("samples", [])
    samples.append(sample)
    # Keep last 30 days
    if len(samples) > 30:
        samples = samples[-30:]

    # Count consecutive zero-star samples
    zero_star_count = 0
    for s in reversed(samples):
        # Check if stars are the same as the first sample
        if len(samples) > 0 and s.get("codeberg_stars", 0) == samples[0].get("codeberg_stars", 0):
            zero_star_count += 1
        else:
            break

    chronic = zero_star_count >= ZERO_STAR_DAYS_FOR_CHRONIC

    # Check if we should emit a recommendation
    now_dt = now
    last_rec_str = previous.get("last_recommendation_at")
    last_rec = _parse_iso_date(last_rec_str) if last_rec_str else None
    emit_recommendation = False
    recommendation_level = "none"

    if chronic and downloads_day >= MIN_DOWNLOADS_PER_DAY_FOR_CONCERN:
        if last_rec is None or (now_dt - last_rec).days >= CHRONIC_RECOMMENDATION_INTERVAL_DAYS:
            if zero_star_count >= CTA_WEAKNESS_DAYS_FOR_ESCALATION:
                recommendation_level = "structural"
                emit_recommendation = True
            elif zero_star_count >= CTA_STRENGTHENING_THRESHOLD:
                recommendation_level = "strengthen"
                emit_recommendation = True

    return {
        "first_sample_at": previous.get("first_sample_at") or now.isoformat(),
        "chronic_started_at": previous.get("chronic_started_at") or (now.isoformat() if chronic else None),
        "samples": samples,
        "zero_star_samples": zero_star_count,
        "chronic": chronic,
        "recommendation_level": recommendation_level,
        "emit_recommendation": emit_recommendation,
        "last_recommendation_at": now.isoformat() if emit_recommendation else previous.get("last_recommendation_at"),
        "recommendation_count": previous.get("recommendation_count", 0) + (1 if emit_recommendation else 0),
        "downloads_day": downloads_day,
        "downloads_month": downloads_month,
        "codeberg_stars": codeberg_stars,
    }


def _emit_recommendation(
    level: str,
    current: dict[str, Any],
    cta_verification: dict[str, Any],
    conversion_data: dict[str, Any],
    now: datetime,
) -> Path | None:
    """Emit a star-conversion strengthening recommendation to shared_findings."""
    downloads_day = conversion_data.get("downloads_day", 0)
    downloads_month = conversion_data.get("downloads_month", 0)
    codeberg_stars = conversion_data.get("codeberg_stars", 0)
    zero_days = conversion_data.get("zero_star_samples", 0)

    lines = [
        f"# Star Conversion Finding — {now.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"**Level**: {level.upper()}",
        f"**Gap**: {downloads_month} downloads/month ({downloads_day}/day) → {codeberg_stars} Codeberg stars ({zero_days} days flat)",
        f"**Conversion ratio**: 0.00% — zero delta across {conversion_data.get('samples', [])} samples",
        "",
        "## Current CTA Status",
    ]

    if not cta_verification.get("runner_cta_exists"):
        lines.append("- ⚠️ CODEBERG_STAR_CTA not found in runner.py — CTA is silent")
    if not cta_verification.get("onboarding_import_works"):
        lines.append("- ⚠️ ralph.onboarding module not resolvable — CODEBERG_STAR_CTA import will fail")
    if cta_verification.get("cta_issues"):
        for issue in cta_verification["cta_issues"]:
            lines.append(f"- ⚠️ {issue}")

    if cta_verification.get("runner_cta_exists") and cta_verification.get("onboarding_import_works"):
        lines.append(f"- ✅ Periodic CTA fires at {cta_verification.get('cta_frequency', '50%')} of runs")
        cta_text = cta_verification.get("cta_text", "")
        if cta_text:
            lines.append(f"- Current message: `{cta_text}`")

    lines.extend([
        "",
        "## Recommendation",
    ])

    if level == "structural":
        lines.extend([
            f"- **Increase CTA frequency**: 20% → 50% (hash-based modulo 2 instead of 5)",
            "- **Add CLI star command**: `ralph star` that opens Codeberg in browser and prints CTA",
            "- **Add README-first CTA**: Update README.md with prominent star/contribute section",
            "- **Add pip post-install message**: Star repo CTA printed on `pip install`",
            "",
            f"**Deadline**: {zero_days} consecutive days of 0-star movement with {downloads_day}/day active users.",
            "This is the highest-leverage autonomous marketing action available — convert existing users.",
        ])
    elif level == "strengthen":
        lines.extend([
            "- Increase CTA frequency in runner.py: 20% → 33% (hash-based modulo 3 instead of 5)",
            "- Verify CODEBERG_STAR_CTA message is compelling and actionable",
            "- Add star CTA to README.md if not already present",
        ])

    lines.extend([
        "",
        "## Conversion Data",
        "```json",
        json.dumps({
            "downloads_month": downloads_month,
            "downloads_day": downloads_day,
            "codeberg_stars": codeberg_stars,
            "zero_star_days": zero_days,
            "cta_verification": cta_verification,
        }, indent=2, default=str),
        "```",
        "",
        f"Generated by star_conversion_agent.py at {now.isoformat()}",
    ])

    content = "\n".join(lines) + "\n"
    STAR_CONVERSION_FINDING.parent.mkdir(parents=True, exist_ok=True)
    STAR_CONVERSION_FINDING.write_text(content, encoding="utf-8")
    return STAR_CONVERSION_FINDING


def _update_blocker_roi_with_star_gap(
    conversion_data: dict[str, Any],
    now: datetime,
) -> None:
    """Update BLOCKER_ROI_SUMMARY.md with current star conversion gap."""
    blocker_path = ROOT / "BLOCKER_ROI_SUMMARY.md"
    marketing_blocker_path = AGENTS_DIR / "BLOCKER_ROI_SUMMARY.md"

    downloads_month = conversion_data.get("downloads_month", 0)
    downloads_day = conversion_data.get("downloads_day", 0)
    codeberg_stars = conversion_data.get("codeberg_stars", 0)
    zero_days = conversion_data.get("zero_star_samples", 0)

    star_gap_block = (
        f"\n### Star Conversion Gap (star_conversion_agent — {now.strftime('%Y-%m-%d %H:%M')})\n"
        f"- **Gap**: {downloads_month} PyPI downloads/month ({downloads_day}/day) → {codeberg_stars} Codeberg stars\n"
        f"- **Conversion rate**: 0.00% across {zero_days} consecutive measurement samples\n"
        f"- **Action**: star_conversion_agent.py monitoring daily; runner.py periodic CTA fires at 50% of runs\n"
        f"- **Next step**: Increase CTA frequency → 50% if gap persists 14+ days\n"
    )

    for bp in [blocker_path, marketing_blocker_path]:
        if bp.exists():
            text = bp.read_text(encoding="utf-8")
            # Remove old star conversion gap block if present
            import re
            pattern = r"\n### Star Conversion Gap.*?(?=\n###|\n---|\Z)"
            text = re.sub(pattern, "", text, flags=re.DOTALL)
            # Insert before "## Contact" or append
            if "## Contact" in text:
                text = text.replace("## Contact", star_gap_block + "\n## Contact")
            else:
                text += star_gap_block
            bp.write_text(text, encoding="utf-8")


def main() -> int:
    now = datetime.now(timezone.utc).astimezone().replace(tzinfo=None)
    print(f"[star_conversion_agent] Running at {now.isoformat()}", flush=True)

    # 1. Read current adoption metrics
    current = _read_adoption_md()
    print(f"  Codeberg: {current['codeberg']['stars']} stars, PyPI: {current['pypi']['downloads_day']}/day", flush=True)

    # 2. Load previous conversion data
    previous = _load_previous_conversion()

    # 3. Verify runner.py CTA is functional
    cta_verification = _verify_runner_cta(now)
    if cta_verification.get("cta_issues"):
        for issue in cta_verification["cta_issues"]:
            print(f"  ⚠️ CTA issue: {issue}", flush=True)
    else:
        print(f"  ✅ Runner CTA functional at {cta_verification.get('cta_frequency', '50%')}", flush=True)

    # 4. Compute conversion status
    conversion_data = _compute_conversion_status(current, previous, now)
    print(f"  Zero-star streak: {conversion_data['zero_star_samples']} days", flush=True)
    print(f"  Chronic: {conversion_data['chronic']}, Level: {conversion_data['recommendation_level']}", flush=True)

    # 5. Save updated conversion data
    _save_conversion(conversion_data)

    # 6. Update BLOCKER_ROI_SUMMARY.md with star gap
    if conversion_data.get("chronic"):
        _update_blocker_roi_with_star_gap(conversion_data, now)
        print(f"  Updated BLOCKER_ROI_SUMMARY.md with star conversion gap", flush=True)

    # 7. Emit recommendation if needed
    if conversion_data.get("emit_recommendation"):
        finding_path = _emit_recommendation(
            conversion_data["recommendation_level"],
            current,
            cta_verification,
            conversion_data,
            now,
        )
        if finding_path:
            print(f"  ✅ Emitted {conversion_data['recommendation_level']} recommendation → {finding_path}", flush=True)
    else:
        print(f"  Recommendation suppressed (level={conversion_data['recommendation_level']}, emit=False)", flush=True)

    # 8. Write summary to log
    log_entry = {
        "timestamp": now.isoformat(),
        "agent": "star_conversion_agent",
        "codeberg_stars": conversion_data["codeberg_stars"],
        "downloads_day": conversion_data["downloads_day"],
        "downloads_month": conversion_data["downloads_month"],
        "zero_star_samples": conversion_data["zero_star_samples"],
        "chronic": conversion_data["chronic"],
        "recommendation_level": conversion_data["recommendation_level"],
        "cta_issues": cta_verification.get("cta_issues", []),
        "cta_functional": bool(
            cta_verification.get("runner_cta_exists")
            and cta_verification.get("onboarding_import_works")
            and not cta_verification.get("cta_issues")
        ),
    }
    log_path = LOG_DIR / f"star_conversion_{now.strftime('%Y-%m-%d_%H%M%S')}.json"
    log_path.write_text(json.dumps(log_entry, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"  Log: {log_path}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
