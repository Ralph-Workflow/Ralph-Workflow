#!/usr/bin/env python3
"""Deterministic OUTCOME reader — closes the learn loop the LLM keeps skipping.

The third floor. Fetch floor guarantees DATA; activation floor guarantees SENDS; this floor
guarantees the LOOP CLOSES: it reads the live open/reply/bounce split of every active or
already-sent Ralph-AB experiment, writes a measurement ledger line, names the LEADING positioning
angle (by reply rate, then open rate), records any real replies to customer_discovery.jsonl, and
raises a deliverability alarm if a live sequence's bounce climbs >3%. The marketer/evaluator then
ACT on that measurement instead of hand-waving "attribute replies."

It is a measurement, not theater: it only writes when there is real send activity (sent > 0), and
each line carries the concrete numbers that should drive the next positioning decision.

Exit codes: 0 always (best-effort, never blocks the gate).
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKETING = ROOT / "agents" / "marketing"
LOGS = MARKETING / "logs"
TOOLS_PATH = ROOT / "TOOLS.md"
LEDGER = LOGS / "tactic_ledger.jsonl"
DISCOVERY = LOGS / "customer_discovery.jsonl"
BASE = "https://api.apollo.io/api/v1"
NAME_PREFIX = "Ralph-AB-"
BOUNCE_CEILING = 3.0


def log(*p: object) -> None:
    print("[apollo-outcome]", *p, flush=True)


def read_api_key() -> str | None:
    try:
        text = TOOLS_PATH.read_text(encoding="utf-8")
        m = re.search(r"###\s*Apollo\.io(.*?)(?:\n###\s|\Z)", text, re.S)
        section = m.group(1) if m else text
        km = (re.search(r"API key[^\n]*?:\*\*\s*`([^`]+)`", section)
              or re.search(r"API key[^\n]*?`([^`]+)`", section)
              or re.search(r"`([A-Za-z0-9_-]{20,})`", section))
        return km.group(1).strip() if km else None
    except Exception:  # noqa: BLE001
        return None


def api(key: str, path: str, body: dict | None, method: str = "POST") -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-Api-Key": key},
        method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def append(path: Path, obj: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj) + "\n")


def main() -> int:
    key = read_api_key()
    if not key:
        log("no API key — skip")
        return 0
    try:
        cs = api(key, "/emailer_campaigns/search", {"per_page": 50}).get("emailer_campaigns", [])
    except Exception as e:  # noqa: BLE001
        log("sequence list failed:", e)
        return 0

    ab = [c for c in cs if str(c.get("name", "")).startswith(NAME_PREFIX)]
    # only our experiments that have actually sent or are live
    live = [c for c in ab if (c.get("unique_sent") or c.get("unique_delivered") or 0) > 0
            or c.get("active")]
    if not any((c.get("unique_sent") or c.get("unique_delivered") or 0) > 0 for c in live):
        log(f"{len(ab)} Ralph-AB sequences, none with sends yet — nothing to measure (clean no-op)")
        return 0

    rows = []
    for c in sorted(live, key=lambda c: c.get("name", "")):
        sent = c.get("unique_sent") or c.get("unique_delivered") or 0
        # D25 (evaluator 2026-06-10): Apollo has TWO open-count fields —
        # `unique_opened` (MPP-filtered, often 0 because Apple MPP pre-fetches every send
        # and the tracked pixel never fires for the human recipient) and
        # `unique_opened_unfiltered` (raw, MPP-uncorrected, the real human-open count).
        # Reading `unique_opened` and reporting `0op` in the cron log hides the real
        # signal (V3=5 unfiltered opens on 16 delivered, V9=1 on 5 delivered). The
        # scorecard headline was just fixed (apollo_scorecard.py) to surface both
        # numbers; the cron-log line is the same defect. Fix: track BOTH and report
        # the unfiltered as the headline open with the tracked count in parens.
        opened = c.get("unique_opened_unfiltered") or 0
        opened_tracked = c.get("unique_opened") or 0
        replied = c.get("unique_replied") or 0
        bounced = c.get("unique_bounced") or 0
        angle = c.get("name", "").replace(NAME_PREFIX, "").rsplit("-", 1)[0]
        rows.append({
            "name": c.get("name"), "id": c.get("id"), "angle": angle, "active": bool(c.get("active")),
            "sent": sent, "opened": opened, "opened_tracked": opened_tracked,
            "replied": replied, "bounced": bounced,
            "open_rate": round(100 * opened / sent, 1) if sent else 0.0,
            "reply_rate": round(100 * replied / sent, 1) if sent else 0.0,
            "bounce_rate": round(100 * bounced / sent, 1) if sent else 0.0,
        })

    # LEADING angle — STATISTICAL HONESTY (EXPERIMENT DESIGN STANDARDS, APOLLO_PLAYBOOK):
    # a "winner" may only be declared at >=MIN_ARM_N delivered per compared arm AND
    # >=MIN_LEADER_REPLIES replies on the leader. Below that, n is qualitative-discovery
    # territory (n=2 "A/B tests" were the owner-caught failure); individual replies are
    # reported as LEARNINGS to read, never as rate comparisons. Opens alone never pick a
    # winner (Apple MPP makes open-rates noise).
    MIN_ARM_N, MIN_LEADER_REPLIES = 30, 3
    sent_rows = [r for r in rows if r["sent"] > 0]
    signal_rows = [r for r in sent_rows if r["replied"] > 0 or r["opened"] > 0 or r["opened_tracked"] > 0]
    powered = (sent_rows and min(r["sent"] for r in sent_rows) >= MIN_ARM_N)
    leader = None
    if powered and signal_rows:
        cand = max(signal_rows, key=lambda r: (r["replied"], r["open_rate"]))
        if cand["replied"] >= MIN_LEADER_REPLIES:
            leader = cand
    replies_total = sum(r["replied"] for r in rows)
    # SAMPLE-AWARE bounce alarm (D16/V1 incident: 1 bounce at n=2 read as "50% > 3% -> abort"
    # stranded 12 verified contacts — tiny-n panic). Alarm = >=3 bounces at any n, or the
    # rate ceiling once n>=10. A single bounce at tiny n is list hygiene, not an angle verdict.
    hot = [r for r in rows if r["bounced"] >= 3
           or (r["sent"] >= 10 and r["bounce_rate"] > BOUNCE_CEILING)]

    summary = " | ".join(
        f"{r['angle']}: {r['sent']}sent/{r['opened']}op[{r['opened_tracked']}tracked]/{r['replied']}rep "
        f"(open {r['open_rate']}%, reply {r['reply_rate']}%, bounce {r['bounce_rate']}%)"
        for r in rows)
    note_bits = []
    if leader:
        note_bits.append(f"LEADING ANGLE = '{leader['angle']}' "
                         f"({leader['replied']} replies, {leader['open_rate']}% open, "
                         f"n>={MIN_ARM_N}/arm — statistically defensible). "
                         f"Double down on this positioning; kill the laggards.")
    elif replies_total > 0:
        note_bits.append(f"QUALITATIVE PHASE (n too small for rate claims): {replies_total} "
                         f"REPL{'Y' if replies_total == 1 else 'IES'} arrived — READ them now, "
                         f"log each as a customer_discovery.jsonl learning (R3). "
                         f"No winner claims below {MIN_ARM_N}/arm + {MIN_LEADER_REPLIES} replies.")
    if hot:
        note_bits.append("⚠️ DELIVERABILITY: " + ", ".join(
            f"{r['angle']} bounce {r['bounce_rate']}%" for r in hot) + " — PAUSE + diagnose.")
    if not note_bits:
        total = sum(r["sent"] for r in rows)
        note_bits.append(f"QUALITATIVE PHASE: {total} delivered, no replies yet — give it the "
                         f"business-hours window. Build the >=30-verified-contacts-per-angle "
                         f"pipeline meanwhile; rates mean nothing at this n.")

    append(LEDGER, {
        "date": date.today().isoformat(),
        "tactic": "apollo_ab_outcome_read",
        "channel": "apollo-sequences",
        "expected_signal": "open/reply split per positioning angle -> winning angle",
        "observed": summary,
        "verdict": "worked",
        "note": " ".join(note_bits),
        "source": "apollo_outcome_reader.py",
        "checkback": date.today().isoformat(),
    })
    log("measured:", summary)
    if note_bits:
        log(note_bits[0])

    # record real replies as discovery signal (best-effort)
    for r in rows:
        if r["replied"] > 0:
            try:
                append(DISCOVERY, {
                    "date": date.today().isoformat(), "source": "apollo_sequence_reply",
                    "sequence": r["name"], "angle": r["angle"], "replies": r["replied"],
                    "note": "Real reply to a positioning A/B sequence — read the actual Apollo "
                            "reply, log the customer language, attribute toward stars."})
            except Exception:  # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
