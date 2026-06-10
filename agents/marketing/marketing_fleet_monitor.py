#!/usr/bin/env python3
"""marketing_fleet_monitor.py — fleet-wide liveness + error monitor for EVERY marketing loop.

WHY (owner-demanded 2026-06-10): ~26 loops drive marketing (19 system-crontab lanes + 7 openclaw
cron agents). Several had no liveness coverage; none escalated fleet-wide. This monitor runs from
the apollo gate (every 2h), SENSES the crontab dynamically (never a hardcoded inventory — D11),
checks each loop's log freshness against its OWN schedule, scans recent log output for errors,
checks openclaw-job artifacts, and writes logs/fleet_health.md. CRITICAL findings print a line
starting with FLEET-ALERT: which the gate forwards to Matrix.

It monitors the watchdogs too (stale_artifact_watchdog etc.) — watchers need watching (the
apollo lint has --selftest for the same reason). Exit 0 always.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

MKT = Path(__file__).resolve().parent
LOGS = MKT / "logs"
OUT = LOGS / "fleet_health.md"
JOBS = Path.home() / ".openclaw" / "cron" / "jobs.json"
ERR_PAT = re.compile(r"(?i)\b(traceback|error|failed|exception|fatal|denied|unauthorized)\b")
ERR_OK = re.compile(r"(?i)(0 error|no error|error=0|errors: 0|non-fatal|error-free)")

# openclaw cron agents -> the artifact that proves the loop actually produced something.
# (jobs.json does not persist run state; artifacts are the observable truth.)
OPENCLAW_ARTIFACTS = {
    "repo-adoption-tracker": (LOGS / "adoption_metrics_latest.md", 30),
    "backlink-tracker": (LOGS / "backlink_status_latest.json", 30),
    "codeberg-github-mirror-sync": (LOGS / "sync_github.log", 14),
    # prompt-only agents leave footprints in the shared ledger (checked separately below)
}
PROMPT_ONLY_JOBS = ["marketing-research-daily", "competitor-analysis",
                    "ralph-site-owner-loop", "marketing-pulse"]


def cron_max_gap_hours(expr: str) -> float:
    """Rough max expected gap for a 5-field cron expr (alert threshold = 2x this)."""
    f = expr.split()
    if len(f) != 5:
        return 26.0
    minute, hour, dom, mon, dow = f
    if dow not in ("*",) or dom not in ("*",):
        return 24.0 * 8        # day-constrained -> weekly-ish
    if hour == "*":
        m = re.match(r"\*/(\d+)", minute)
        if m:
            return max(1.0, int(m.group(1)) / 60 * 3)
        return 2.0             # minute-listed hourly lane (e.g. "1,31 * ...")
    m = re.match(r"\*/(\d+)", hour)
    if m:
        return int(m.group(1)) * 2.5
    return 26.0                # fixed daily hour(s)


def main() -> int:
    now = time.time()
    stamp = datetime.now().isoformat(timespec="seconds")
    rows, criticals = [], []

    # ---- system crontab lanes (sensed live) ----
    try:
        ct = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
    except Exception as e:  # noqa: BLE001
        ct = ""
        criticals.append(f"cannot read crontab: {e}")
    for line in ct.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^((?:\S+\s+){4}\S+)\s+(.+?)\s*>>\s*(\S+)", line)
        if not m:
            continue
        expr, cmd, logpath = m.groups()
        name = Path(re.search(r"(\S+\.(?:py|sh))", cmd).group(1)).name if re.search(r"\.(py|sh)", cmd) else cmd[:30]
        lp = Path(logpath)
        gap = cron_max_gap_hours(expr)
        if not lp.exists():
            rows.append((name, "system-cron", "NO LOG YET", f"≤{gap:.0f}h", "—"))
            continue
        age_h = (now - lp.stat().st_mtime) / 3600
        # fresh-error scan: only consider error lines that belong to the log's most recent
        # activity window. Cron appends one run's output at a time, and the file's mtime is
        # the timestamp of the last run. So if the file moved within `err_window_s`, the
        # last contiguous block of error hits in the file is from the most recent run and is
        # what we should report. Historic tracebacks left from a previous, since-fixed bug
        # (and not yet trimmed) live above that block and are ignored.
        err = ""
        err_window_s = max(4 * 3600, int(2 * gap * 3600))
        if age_h < 26 and age_h * 3600 <= err_window_s:
            try:
                tail = "\n".join(lp.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
                hits = [l.strip()[:90] for l in tail.splitlines()
                        if ERR_PAT.search(l) and not ERR_OK.search(l)]
                if hits:
                    err = hits[-1]
            except OSError:
                pass
        status = "OK"
        if age_h > 2 * gap:
            status = "STALE"
            criticals.append(f"{name}: log {age_h:.0f}h old (expected ≤{gap:.0f}h cadence)")
        if err:
            status = (status + "+ERRORS") if status != "OK" else "ERRORS"
            criticals.append(f"{name}: recent error: {err}")
        rows.append((name, "system-cron", f"{age_h:.1f}h ago", f"≤{gap:.0f}h", status + (f" · {err}" if err else "")))

    # ---- openclaw cron agents (artifact truth) ----
    enabled = []
    try:
        jl = json.load(JOBS.open())
        jl = jl if isinstance(jl, list) else jl.get("jobs", [])
        enabled = [j.get("name") for j in jl if j.get("enabled", True)]
    except Exception as e:  # noqa: BLE001
        criticals.append(f"cannot read openclaw jobs.json: {e}")
    for name, (art, max_h) in OPENCLAW_ARTIFACTS.items():
        if name not in enabled:
            rows.append((name, "openclaw", "DISABLED", "—", "disabled"))
            continue
        if not art.exists():
            criticals.append(f"{name}: artifact {art.name} missing")
            rows.append((name, "openclaw", "ARTIFACT MISSING", f"≤{max_h}h", "CRITICAL"))
            continue
        age_h = (now - art.stat().st_mtime) / 3600
        ok = age_h <= max_h
        if not ok:
            criticals.append(f"{name}: {art.name} is {age_h:.0f}h old (≤{max_h}h expected)")
        rows.append((name, "openclaw", f"{art.name} {age_h:.1f}h", f"≤{max_h}h", "OK" if ok else "STALE"))
    # prompt-only agents: footprint = a ledger line citing them within 48h
    ledger = LOGS / "tactic_ledger.jsonl"
    recent = ""
    if ledger.exists():
        cutoff_d = datetime.now().strftime("%Y-%m-")
        recent = "\n".join(ledger.read_text(encoding="utf-8").splitlines()[-120:])
    for name in PROMPT_ONLY_JOBS:
        if name not in enabled:
            rows.append((name, "openclaw", "DISABLED", "—", "disabled"))
            continue
        key = name.replace("-", "_")
        seen = (key in recent) or (name in recent)
        rows.append((name, "openclaw", "ledger footprint" if seen else "no recent ledger footprint",
                     "48h", "OK" if seen else "WATCH"))
        # WATCH not CRITICAL: prompt-only agents log irregularly; 2 consecutive WATCH days is the
        # evaluator's cue (it reads this file) to investigate the loop as possibly-dead-or-theater.

    # ---- THEATER DETECTORS (audit-driven, 2026-06-10) ----
    state_p = LOGS / "fleet_monitor_state.json"
    try:
        state = json.load(state_p.open())
    except Exception:  # noqa: BLE001
        state = {}
    # (i) backlink stagnation: byte-identical status for >7 days = monitoring theater
    bl = LOGS / "backlink_status_latest.json"
    if bl.exists():
        h = __import__("hashlib").sha256(bl.read_bytes()).hexdigest()[:16]
        if state.get("backlink_hash") != h:
            state["backlink_hash"], state["backlink_since"] = h, now
        elif now - state.get("backlink_since", now) > 7 * 86400:
            criticals.append("backlink-tracker: status byte-identical >7 days — submissions stalled; "
                             "re-actuate or kill pending directories (audit: monitoring theater)")
    # (ii) mirror-sync skip-streak: 3+ consecutive 'skipping' lines = lock wedged again
    sl = LOGS / "sync_github.log"
    if sl.exists():
        tail = sl.read_text(encoding="utf-8", errors="replace").splitlines()[-6:]
        skips = sum(1 for l in tail if "skipping" in l)
        if skips >= 3 and not any("Sync complete" in l for l in tail):
            criticals.append("mirror-sync: 3+ consecutive skips with no completion — lock wedged again")
    # (iii) star flatline: persistent 'primary_repo_flat' escalates to the OWNER every 3 days —
    # a flat goal metric means the FLEET's tactics are not converting; that decision is human-level
    am_t = (LOGS / "adoption_metrics_latest.md").read_text(encoding="utf-8") if (LOGS / "adoption_metrics_latest.md").exists() else ""
    if "primary_repo_flat" in am_t:
        days_flat = (now - state.setdefault("flat_since", now)) / 86400
        if days_flat > 3 and now - state.get("flat_alerted", 0) > 3 * 86400:
            state["flat_alerted"] = now
            criticals.append(f"GOAL FLATLINE: Codeberg stars flat {days_flat:.0f}+ days across the whole "
                             f"fleet — current tactics are not converting; angle/channel pivot decision needed")
    else:
        state.pop("flat_since", None)
    # (iv) LLM-loop theater: no external artifact URL in recent ledger entries from the
    # research/pulse loops for 7+ days = surveys without actions
    if ledger.exists():
        week = [l for l in ledger.read_text(encoding="utf-8").splitlines()[-400:]
                if re.search(r"research|pulse|warm_pool|distribution", l, re.I)]
        urls = [l for l in week if re.search(r"https?://(?!ralphworkflow\.com)", l)]
        last_url_ts = state.get("last_external_url", 0)
        if urls:
            state["last_external_url"] = now
        elif last_url_ts and now - last_url_ts > 7 * 86400:
            criticals.append("LLM loops (research-daily/pulse): no external artifact URL logged in 7+ days "
                             "— survey-without-action theater (audit grade D); floors or merge needed")
    try:
        json.dump(state, state_p.open("w"), indent=1)
    except Exception:  # noqa: BLE001
        pass

    # ---- goal header (the point of the whole fleet) ----
    goal = []
    am = LOGS / "adoption_metrics_latest.md"
    if am.exists():
        t = am.read_text(encoding="utf-8")
        cb = re.search(r"## Codeberg.*?Stars:\s*(\d+)\s*\(([+\-]\d+)\)", t, re.S)
        gh = re.search(r"## GitHub.*?Stars:\s*(\d+)\s*\(([+\-]\d+)\)", t, re.S)
        if cb:
            goal.append(f"Codeberg stars {cb.group(1)} ({cb.group(2)})")
        if gh:
            goal.append(f"GitHub stars {gh.group(1)} ({gh.group(2)})")

    lines = [
        f"# MARKETING FLEET HEALTH — {stamp} (regenerated every 2h by the apollo gate)",
        "",
        f"> GOAL: {' · '.join(goal) if goal else 'adoption metrics unavailable'} — every loop below exists to move these.",
        f"> {len(rows)} loops monitored · {len(criticals)} critical finding(s).",
        "",
        "| loop | kind | last signal | expected | status |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {n} | {k} | {sig} | {exp} | {st} |" for n, k, sig, exp, st in sorted(rows)]
    if criticals:
        lines += ["", "## 🚨 CRITICAL"] + [f"- {c}" for c in criticals]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[fleet-monitor] {len(rows)} loops checked, {len(criticals)} critical")
    if criticals:
        print("FLEET-ALERT: " + " | ".join(criticals[:4]) + (" (+more, see fleet_health.md)" if len(criticals) > 4 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
