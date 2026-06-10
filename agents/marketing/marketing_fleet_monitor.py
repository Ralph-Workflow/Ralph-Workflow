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
        # Resolve the actual script path the cron entry would exec (D47: handle
        # renamed-to-.disabled or pruned scripts; surface as STALE-PRUNED, not as a
        # phantom critical). cmd may be "python3 agents/marketing/foo.py" — we want
        # the foo.py part. We do NOT trust the .py basename if the file is actually
        # .py.disabled (cron wouldn't have found it on next launch).
        mscript = re.search(r"(\S+\.(?:py|sh))", cmd)
        name = Path(mscript.group(1)).name if mscript else cmd[:30]
        lp = Path(logpath)
        gap = cron_max_gap_hours(expr)
        # D47: if the underlying script was renamed to .disabled (known-pruned) or
        # deleted, the cron entry is dead but the monitor would otherwise show
        # 'NO LOG YET' and never resolve. Detect both forms and mark STALE-PRUNED.
        if mscript:
            script_path = Path(mscript.group(1))
            if not script_path.exists() and Path(str(script_path) + ".disabled").exists():
                rows.append((name, "system-cron", "PRUNED (.disabled)", "—", "STALE-PRUNED"))
                continue
            if not script_path.exists():
                rows.append((name, "system-cron", "PRUNED (no file)", "—", "STALE-PRUNED"))
                continue
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

    # (v) PHANTOM-BLOCKER detector (D36, marketing review 2026-06-10; D52 STALE-SCAN FIX 2026-06-10):
    # the marketer used to defer the highest-ROI warm-pool/H-item work with "staged until the GitHub
    # token is resolved/provisioned" — but `gh` is authed (repo scope). A fabricated blocker is
    # warm-pool activity-theater. The fix (D52): scope the scan to the last 24h of ledger lines
    # (NOT the last 60 lines regardless of date). Old corrections ("github=UNBLOCKED" 2026-06-08) used
    # to fire the alert every 2h forever; the date prefix `^.*"date":\s*"YYYY-MM-DD"` filter makes
    # the scan a 24h window and stops the stale alert. Alert text also updated to the D36-revised +
    # D46-revised binding (DRAFT public writes for owner, do not POST).
    phantom = re.compile(r"(?i)(staged|blocked|defer).{0,40}(github|gh)\b.{0,30}(token|auth).{0,30}"
                         r"(resolv|provision|set ?up|unresolved|missing)")
    # Build a 24h-scoped ledger slice: parse the JSON `date` field, keep only entries from the last 24h.
    ledger_path = LOGS / "tactic_ledger.jsonl"
    recent_24h_blob = ""
    if ledger_path.exists():
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        cutoff = _dt.now(_tz.utc) - _td(hours=24)
        kept = []
        for line in ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line).get("date", "")
                if d and _dt.strptime(d, "%Y-%m-%d") >= cutoff.replace(hour=0, minute=0, second=0, microsecond=0):
                    kept.append(line)
            except Exception:
                continue
        recent_24h_blob = "\n".join(kept)
    blob = recent_24h_blob + "\n" + ((LOGS / "segments.md").read_text(encoding="utf-8", errors="replace")
                            if (LOGS / "segments.md").exists() else "")
    # D52b FALSE-POSITIVE FILTER: the new PUBLIC-WRITE CONDUCT uses the word "STAGED" + "owner" +
    # "DRAFT" as POSITIVE framings (the work is staged for the owner to post). The old phantom-
    # blocker regex caught those. Strip lines that contain a "for owner", "PENDING_OWNER",
    # "DRAFT", "drafts/", or "DRAFTED" frame BEFORE searching — those are NOT phantom blockers,
    # they are the correct D46-revised state.
    lines_to_check = [ln for ln in blob.splitlines() if not re.search(
        r"(?i)(for\s+owner|pending_owner|^.*drafts/|drafted|\\bdraft\\b|owner will post|owner posts)", ln
    )]
    blob_filtered = "\n".join(lines_to_check)
    if phantom.search(blob_filtered):
        criticals.append("phantom-blocker LIVE (D36, 24h-scan, D52b-filtered): a 24h artifact "
                         "defers warm-pool/H-item work on a 'GitHub token unresolved' premise — "
                         "but gh IS authed (repo scope) for READ; DRAFT public writes for the "
                         "owner, do NOT post them yourself (D46-revised). Engage via gh for "
                         "discovery; do not fabricate a 'token unresolved' blocker.")

    # (vi) AUTONOMOUS PUBLIC-WRITE detector (D46, owner-mandated 2026-06-10): the loop must NEVER
    # post to GitHub/public surfaces itself (gh-write-guard blocks gh; this catches any bypass —
    # /usr/bin/gh, a future PATH change, a different tool). Flag a ledger footprint of an actual
    # public write TODAY so the owner knows immediately a reputation action happened autonomously.
    pubwrite = re.compile(r"(?i)\b(posted|fired|opened|created|commented on)\b.{0,40}"
                          r"(issue|pr|pull request|comment|discussion)\b|gh (issue|pr) (create|comment)")
    today = datetime.now().strftime("%Y-%m-%d")
    todays = [l for l in (recent.splitlines() if recent else []) if today in l]
    pw_hits = [l for l in todays if pubwrite.search(l)]
    if pw_hits:
        criticals.append(f"AUTONOMOUS PUBLIC-WRITE footprint TODAY (D46): {len(pw_hits)} ledger line(s) "
                         f"indicate the loop posted to a public surface itself — this must be HUMAN-only. "
                         f"Verify the gh-write-guard is intact and on PATH; the owner posts, the loop suggests.")
    # also surface if the gh-write-guard logged blocked attempts (the loop is TRYING to post → prompt drift)
    gwlog = LOGS / "gh_write_guard_blocks.jsonl"
    if gwlog.exists():
        gw_today = [l for l in gwlog.read_text(encoding="utf-8").splitlines() if today in l]
        if len(gw_today) > 2:
            criticals.append(f"gh-write-guard blocked {len(gw_today)} write attempt(s) today — the marketer "
                             f"is still TRYING public writes despite PUBLIC-WRITE CONDUCT; tighten the prompt.")

    # (vii) gh-write-guard INTEGRITY (D46): the deterministic block only works while the shim exists
    # and carries its guard logic. If it's missing/gutted, autonomous public writes become possible
    # again — alert loudly (watchers need watching, like the lint --selftest).
    import os as _os
    shim_ok = False
    for _d in ("/home/mistlight/.local/share/pnpm/bin/gh", "/home/mistlight/.bun/bin/gh",
               "/home/mistlight/.cargo/bin/gh", "/home/mistlight/.opencode/bin/gh"):
        try:
            if _os.path.exists(_d) and "gh-write-guard" in Path(_d).read_text(encoding="utf-8"):
                shim_ok = True
        except Exception:  # noqa: BLE001
            pass
    if not shim_ok:
        criticals.append("gh-write-guard SHIM MISSING/GUTTED (D46): the deterministic block on autonomous "
                         "public GitHub writes is gone — reinstall it first in the gateway PATH before the "
                         "next marketer turn, or the loop can post publicly again.")

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
