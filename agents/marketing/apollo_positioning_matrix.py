#!/usr/bin/env python3
"""apollo_positioning_matrix.py — the ACCUMULATING ANSWER to "which positioning × wording works
for which ICP" (deterministic, regenerated every gate run).

WHY (owner-demanded 2026-06-09: "figuring out which marketing positioning and wording works for
which ICP — WILL I GET THIS KNOWLEDGE AFTER RUNNING THIS LOOP???"): the loop had the structure
(angle-variant sequences, per-angle outcome reads, qualitative learnings) but NO accumulator —
the knowledge lived scattered across ledger lines and prose. This file IS the deliverable: every
run it recomputes the angle×ICP matrix from live sequence stats + validated learnings, states
plainly WHAT WE KNOW and WHAT WE DO NOT KNOW YET, and only ever claims a winner at the
statistical thresholds (30/arm + 3 replies; real wave = ~200). The marketer reads it (STEP 1),
refines positioning FROM it, and the evaluator polices that it MOVES run-over-run.

Status ladder per arm: UNTESTED -> FEEDING (delivered<30, no replies) -> QUALITATIVE
(replies at n<30: each reply is a learning, no rate claims) -> POWERED (>=30/arm) ->
WINNER/KILLED (only at thresholds). Exit 0 always.
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime
from pathlib import Path

MKT = Path(__file__).resolve().parent
LOGS = MKT / "logs"
OUT = LOGS / "positioning_matrix.md"
DISCOVERY = LOGS / "customer_discovery.jsonl"
TOOLS = MKT.parent.parent / "TOOLS.md"
BASE = "https://api.apollo.io/api/v1"
MIN_ARM_N, MIN_LEADER_REPLIES, REAL_WAVE = 30, 3, 200


def api_key() -> str | None:
    try:
        m = re.search(r"\b(328[A-Za-z0-9]{10,})\b", TOOLS.read_text(encoding="utf-8"))
        return m.group(1) if m else None
    except Exception:  # noqa: BLE001
        return None


def api(key: str, path: str, body: dict | None = None, method: str = "POST") -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Api-Key": key, "Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def validated_learnings() -> list[dict]:
    rows = []
    if not DISCOVERY.exists():
        return rows
    for ln in DISCOVERY.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        who = str(r.get("who", "")).strip()
        q = str(r.get("quote", "")).strip()
        if (not who or who.lower().startswith(("internal", "aggregate", "synthesized"))
                or not q or q.lower() in ("none", "null", "n/a", "-")
                or re.search(r"(?i)no (direct )?(quotes|replies)", q)
                or re.search(r"(?i)^.{0,60}\b(posted|sent|drafted)\b", q)):  # self-quote = engagement, not learning
            continue
        rows.append(r)
    return rows


def main() -> int:
    stamp = datetime.now().isoformat(timespec="seconds")
    key = api_key()
    arms = []
    if key:
        try:
            cs = api(key, "/emailer_campaigns/search", {"per_page": 50}).get("emailer_campaigns", [])
            for c in cs:
                name = str(c.get("name", ""))
                if not name.startswith("Ralph-AB-"):
                    continue
                m = re.match(r"Ralph-AB-(V\d+)-(.+?)-2026", name)
                arm = {"name": name, "variant": m.group(1) if m else name,
                       "angle_icp": (m.group(2) if m else name).replace("-", " "),
                       "active": bool(c.get("active")),
                       "delivered": c.get("unique_delivered") or 0,
                       "replied": c.get("unique_replied") or 0,
                       "bounced": c.get("unique_bounced") or 0,
                       "spam": c.get("unique_spam_blocked") or 0, "enrolled": None}
                try:
                    arm["enrolled"] = api(key, "/contacts/search", {
                        "emailer_campaign_ids": [c.get("id")], "per_page": 1}
                    ).get("pagination", {}).get("total_entries")
                except Exception:  # noqa: BLE001
                    pass
                arms.append(arm)
        except Exception:  # noqa: BLE001
            pass
    learnings = validated_learnings()
    powered = [a for a in arms if a["delivered"] >= MIN_ARM_N]
    winner = None
    if len(powered) >= 2:
        cand = max(powered, key=lambda a: a["replied"])
        if cand["replied"] >= MIN_LEADER_REPLIES:
            winner = cand

    def status(a: dict) -> str:
        # D27 (marketer-found 2026-06-10 14:00): the matrix was reporting V1 as FEEDING when it is
        # actually R2-DEAD (5 delivered / 3 bounced = 60% bounce). The 3% R2 abort threshold means
        # V1 should be marked DEAD, not FEEDING. A new arm is DEAD when bounce_rate > 30% at n>=3
        # delivered (5x the R2 abort threshold = 'structurally broken' not 'single contact issue').
        if a["delivered"] == 0:
            return "UNTESTED" + ("" if a["active"] else " (inactive)")
        bounce_rate = (a.get("bounced") or 0) / max(a["delivered"], 1)
        if bounce_rate > 0.30 and a["delivered"] >= 3:
            return f"DEAD (R2 {round(bounce_rate*100, 1)}% bounce; do not re-activate)"
        if a["delivered"] < MIN_ARM_N:
            return "QUALITATIVE — replies are learnings, rates are noise" if a["replied"] else \
                f"FEEDING ({a['delivered']}/{MIN_ARM_N} floor; real wave {REAL_WAVE//2}/arm)"
        return "POWERED"

    lines = [
        f"# POSITIONING × ICP MATRIX — what we KNOW about which wording converts (live, {stamp})",
        "",
        "> Auto-accumulated every gate run by apollo_positioning_matrix.py. THIS FILE is the",
        "> program's knowledge deliverable: run the loop → this fills in. Winner claims appear",
        f"> ONLY at >={MIN_ARM_N}/arm + >={MIN_LEADER_REPLIES} replies (real test wave ≈{REAL_WAVE}).",
        "",
        "## The matrix (one row per angle×ICP arm, live Apollo stats)",
        "| Arm | Angle × ICP | enrolled | delivered | replies | bounced | status |",
        "|---|---|---|---|---|---|---|",
    ]
    for a in sorted(arms, key=lambda a: a["variant"]):
        lines.append(f"| {a['variant']} | {a['angle_icp'][:46]} | {a['enrolled'] if a['enrolled'] is not None else '?'} "
                     f"| {a['delivered']} | {a['replied']} | {a['bounced']} | {status(a)} |")
    lines += ["", "## ✅ WHAT WE KNOW SO FAR (evidence-grade only)"]
    known = []
    if winner:
        known.append(f"- **WINNER at power: {winner['variant']} ({winner['angle_icp']})** — "
                     f"{winner['replied']} replies at n={winner['delivered']}. Double down; emphasize "
                     f"this angle in RALPH_WORKFLOW_POSITIONING.md.")
    for a in arms:
        if a["spam"] > 0:
            known.append(f"- {a['variant']} ({a['angle_icp']}): {a['spam']} spam-block(s) — this WORDING "
                         f"trips Apollo's commercial filter for this ICP (V2 incident class); copy lesson, not angle lesson.")
        if a["replied"] > 0 and a["delivered"] < MIN_ARM_N:
            known.append(f"- {a['variant']} ({a['angle_icp']}): {a['replied']} repl{'y' if a['replied']==1 else 'ies'} "
                         f"at n={a['delivered']} — read each as a Mom-Test learning (see below), NOT a rate.")
    for r in learnings[-8:]:
        known.append(f"- LEARNING ({str(r.get('segment',''))[:40]}): \"{str(r.get('quote',''))[:110]}\" "
                     f"→ {str(r.get('implication',''))[:110]}")
    lines += known or ["- Nothing evidence-grade yet — the matrix below shows exactly what's missing."]
    lines += ["", "## ❌ WHAT WE DO NOT KNOW YET (the honest gap list — this drives the next runs)"]
    unknown = []
    for a in arms:
        if a["active"] and a["delivered"] < MIN_ARM_N:
            unknown.append(f"- {a['variant']} ({a['angle_icp']}): n={a['delivered']}/{MIN_ARM_N} floor "
                           f"— no rate conclusions possible; keep feeding (duty 4).")
    if not winner:
        unknown.append("- NO angle×ICP combination has proven conversion yet (no powered comparison; 0 attributable stars).")
    unknown.append("- Star attribution: no per-channel mechanism beyond repo-surface split — a star today is not attributable to an angle.")
    lines += unknown
    lines += ["", "## How the loop uses this file",
              "- MARKETER: read in STEP 1; every positioning/copy refinement cites a row or learning here;",
              "  duty 4 feeds the thinnest active arm; new angles only with a row-level rationale.",
              "- EVALUATOR: this matrix must MOVE run-over-run (n up, learnings added, unknowns shrinking).",
              "  A static matrix across runs = the program is not learning = first-class defect."]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[positioning-matrix] wrote {OUT.name}: {len(arms)} arms, {len(learnings)} validated learnings, "
          f"winner={'none yet' if not winner else winner['variant']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
