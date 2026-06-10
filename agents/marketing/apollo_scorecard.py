#!/usr/bin/env python3
"""Deterministic scorecard — what the Apollo marketing agent is EVALUATED against.

Computes the agent's performance from REAL data (ledger + adoption metrics + artifacts),
not vibes. Written to logs/apollo_scorecard.md and fed into the Layer-2 marketer prompt so
the agent sees how it is doing and improves against it. This is measurement that drives the
next action — NOT a theater report. Run by the gate before the marketer turn.

Criteria (see APOLLO_SCORECARD.md):
  PRIMARY (real external outcomes)  — Codeberg stars delta, attributable replies, new learnings.
  PROCESS (leading indicators)      — ICP confidence, phase-gate progress, worked:flat tactic ratio.
  ANTI-THEATER (must stay low)      — repeated/failing tactics, artifacts that reach nobody.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "agents" / "marketing" / "logs"
LEDGER = LOGS / "tactic_ledger.jsonl"
FINDINGS = LOGS / "icp_findings.md"
METRICS = LOGS / "adoption_metrics_latest.md"
DRAFTS = ROOT / "agents" / "marketing" / "drafts"
DISCOVERY = LOGS / "customer_discovery.jsonl"
OUT = LOGS / "apollo_scorecard.md"


def load_ledger() -> list[dict]:
    rows = []
    if LEDGER.exists():
        for ln in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(ln))
            except Exception:  # noqa: BLE001
                pass
    return rows


def recent(rows: list[dict], days: int) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [r for r in rows if str(r.get("date", "")) >= cutoff]


def sequence_stats() -> dict | None:
    """Best-effort live Apollo sequence stats — the REAL outreach metric (sends/opens/replies/bounces)."""
    import urllib.request
    tools = ROOT / "TOOLS.md"
    try:
        text = tools.read_text(encoding="utf-8")
        m = re.search(r"###\s*Apollo\.io(.*?)(?:\n###\s|\Z)", text, re.S)
        km = re.search(r"API key[^\n]*?`([^`]+)`", m.group(1) if m else text)
        key = km.group(1).strip()
        req = urllib.request.Request(
            "https://api.apollo.io/api/v1/emailer_campaigns/search",
            data=json.dumps({"page": 1, "per_page": 25}).encode(),
            headers={"Content-Type": "application/json", "X-Api-Key": key}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            cs = json.load(resp).get("emailer_campaigns", [])
        # HEADLINE metrics = LIVE MOTION only (currently ACTIVE sequences). The paused burned
        # legacy (Ralph Workflow Seq, tokenmaxxing) is sunk history; including its 20% bounce
        # forever would make the scorecard permanently red and mislead the self-improvement loop.
        # Same for ABORTED Ralph-AB experiments: the abort already stopped their motion, so their
        # frozen bounce stats are diagnosis data (in the ledger), not a live deliverability state —
        # counting V2's 1 bounce forever pinned the headline at 50% after the problem was handled.
        live = [c for c in cs if c.get("active")]
        # ARM FILL — a 2-contact arm is a placeholder, not an experiment (owner-caught 2026-06-09).
        # Count enrolled contacts per active Ralph-AB arm so the 30/arm gap is unmissable every run.
        arm_fill = []
        for c in live:
            if not str(c.get("name", "")).startswith("Ralph-AB-"):
                continue
            try:
                creq = urllib.request.Request(
                    "https://api.apollo.io/api/v1/contacts/search",
                    data=json.dumps({"emailer_campaign_ids": [c.get("id")],
                                     "per_page": 1}).encode(),
                    headers={"Content-Type": "application/json", "X-Api-Key": key}, method="POST")
                with urllib.request.urlopen(creq, timeout=15) as cresp:
                    n_enrolled = json.load(cresp).get("pagination", {}).get("total_entries", 0)
            except Exception:  # noqa: BLE001
                n_enrolled = None
            arm_fill.append((c.get("name"), n_enrolled))
        sent = sum(c.get("unique_sent") or c.get("unique_delivered") or 0 for c in live)
        # D6 + D25 (D6 owner-found 2026-06-09; D25 evaluator-found 2026-06-10):
        # Apollo's `unique_opened` is the MPP-FILTERED tracked-open count, which is often 0
        # because Apple Mail Privacy Protection pre-fetches every send and registers the open
        # before the human ever sees it. The GROUND-TRUTH open count is `unique_opened_unfiltered`
        # (the raw count, MPP-uncorrected). The scorecard was reporting `unique_opened=0` in the
        # headline, hiding the real open signal (e.g. V3 = 5 unfiltered opens on 16 delivered,
        # V9 = 1 on 5 delivered). The right reading: `opened_unfiltered` is the raw count, with
        # the caveat that some of those opens may be bots/MPP. Replies are the cleaner signal,
        # but the headline MUST NOT lie about whether the angle is being read at all. Both
        # numbers are now surfaced; the unfiltered is the headline and the tracked is a footnote.
        opened_tracked = sum(c.get("unique_opened") or 0 for c in live)
        opened = sum(c.get("unique_opened_unfiltered") or 0 for c in live)
        opened_tracked_delivered = sum(c.get("unique_delivered_open_tracked") or 0 for c in live)
        replied = sum(c.get("unique_replied") or 0 for c in live)
        # D17 (owner-found 2026-06-09): hard_bounced is a REAL bounce — the SMTP rejection
        # of a dead address is worse than a soft bounce, and excluding it under-counts the
        # primary domain's deliverability damage. Both count toward the brake.
        bounced = (sum(c.get("unique_bounced") or 0 for c in live)
                   + sum(c.get("unique_hard_bounced") or 0 for c in live))
        hard_bounced = sum(c.get("unique_hard_bounced") or 0 for c in live)
        active = sum(1 for c in cs if c.get("active"))
        ralph_active = sum(1 for c in cs if c.get("active") and str(c.get("name","")).startswith("Ralph-AB-"))
        # D17b: R7 cap is 2 live variants. If >2 Ralph-AB sequences are active, the cap is breached
        # (a manual approve or UI activation path bypassed the floor's GUARD F). Surface loudly.
        r7_violation = ralph_active > 2
        legacy_bounced = sum(c.get("unique_bounced") or 0 for c in cs) - bounced
        # staged-but-never-activated Ralph-AB experiments = the account's #1 theater failure mode
        staged_unsent = sum(1 for c in cs if str(c.get("name", "")).startswith("Ralph-AB-")
                            and not c.get("active") and (c.get("unique_sent") or 0) == 0)
        denom = sent or (opened + bounced) or 1
        return {"sequences": len(cs), "active": active, "ralph_active": ralph_active,
                "sent": sent, "opened": opened, "opened_tracked": opened_tracked,
                "opened_tracked_delivered": opened_tracked_delivered,
                "replied": replied, "bounced": bounced,
                "hard_bounced": hard_bounced, "staged_unsent": staged_unsent,
                "legacy_bounced": legacy_bounced, "arm_fill": arm_fill, "r7_violation": r7_violation,
                "bounce_rate_pct": round(100 * bounced / denom, 1),
                "reply_rate_pct": round(100 * replied / (sent or 1), 1),
                "open_rate_pct_unfiltered": round(100 * opened / (sent or 1), 1),
                "open_rate_pct_tracked": round(100 * opened_tracked / (sent or 1), 1)}
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    rows = load_ledger()
    last7 = recent(rows, 7)
    seq = sequence_stats()

    verdicts: dict[str, int] = {}
    for r in rows:
        verdicts[r.get("verdict", "?")] = verdicts.get(r.get("verdict", "?"), 0) + 1
    worked = verdicts.get("worked", 0)
    flat = verdicts.get("no_effect", 0) + verdicts.get("failing", 0)
    marketer_actions_7d = sum(1 for r in last7 if r.get("tactic") == "apollo_marketer_decision")
    research_7d = sum(1 for r in last7 if r.get("tactic") == "apollo_research")

    # Real external metric: Codeberg stars + delta (PRIMARY), GitHub mirror stars (SECONDARY).
    stars = stars_delta = "n/a"
    gh_stars = gh_stars_delta = "n/a"
    if METRICS.exists():
        t = METRICS.read_text(encoding="utf-8")
        m = re.search(r"Stars:\s*(\d+)\s*\(([+\-]\d+)\)", t)
        if m:
            stars, stars_delta = m.group(1), m.group(2)
        gm = re.search(r"## GitHub.*?Stars:\s*(\d+)\s*\(([+\-]\d+)\)", t, re.DOTALL)
        if gm:
            gh_stars, gh_stars_delta = gm.group(1), gm.group(2)

    # ICP confidence (parsed from findings).
    icp_conf = "unknown"
    if FINDINGS.exists():
        ft = FINDINGS.read_text(encoding="utf-8")
        cm = re.search(r"[Cc]onfidence[:\s*]*\**\s*(low|medium|med|high)", ft, re.IGNORECASE)
        if cm:
            icp_conf = cm.group(1).lower()

    drafts_n = len(list(DRAFTS.glob("*.md"))) if DRAFTS.exists() else 0
    # GATE-METRIC INTEGRITY (2026-06-09): only VALIDATED learnings count toward the Phase 1->2 gate —
    # a real EXTERNAL human (who not internal:/aggregate:/synthesized:) WITH a verbatim quote
    # (CUSTOMER_LEARNING_SYSTEM §3). The audit found 6 raw entries, only 2 real people with quotes;
    # counting self-reflection, aggregates, and delivery logs as "customer learnings" is gate
    # inflation — the same theater class as staged-unsent sequences.
    discovery_raw, discovery_n = 0, 0
    if DISCOVERY.exists():
        for ln in DISCOVERY.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            discovery_raw += 1
            try:
                r = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            who = str(r.get("who", "")).strip().lower()
            if not who or who.startswith(("internal", "aggregate", "synthesized")):
                continue
            q = str(r.get("quote", "")).strip()
            # a quote must be real customer words — null-ish strings, "no quotes yet"
            # placeholders, AND self-quotes (quoting OUR OWN outbound message — the Marco
            # engagement entry — is an engagement log, not a customer learning).
            if (not q or q.lower() in ("none", "null", "n/a", "-", "tbd")
                    or re.search(r"(?i)no (direct )?(quotes|replies)", q)
                    or re.search(r"(?i)^.{0,60}\b(posted|sent|drafted)\b", q)):
                continue
            discovery_n += 1

    # Phase 1 -> 2 gate readiness (per MARKETING_PHASES: need med-high ICP + >=12-15 learnings).
    gate_ready = (icp_conf in ("medium", "med", "high")) and discovery_n >= 12
    gate_status = ("READY" if gate_ready else
                   f"not yet (ICP={icp_conf}, VALIDATED learnings={discovery_n}/12"
                   + (f"; {discovery_raw - discovery_n} raw entries did NOT count: "
                      f"internal/aggregate/synthesized or no verbatim quote" if discovery_raw > discovery_n else "")
                   + ")")

    scorecard = {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "PRIMARY_real_outcomes": {
            "codeberg_stars": stars, "stars_delta": stars_delta,
            "github_stars_secondary": gh_stars, "github_stars_delta": gh_stars_delta,
            "customer_learnings_total": discovery_n,
            "worked_tactics_alltime": worked,
        },
        "PROCESS_leading": {
            "icp_confidence": icp_conf,
            "phase1_to_2_gate": gate_status,
            "marketer_actions_7d": marketer_actions_7d,
            "research_fetches_7d": research_7d,
            "drafts_total": drafts_n,
            "worked_vs_flat": f"{worked}:{flat}",
        },
        "ANTI_THEATER_watch": {
            "failing_tactics": verdicts.get("failing", 0),
            "note": "stars_delta=+0 for weeks => process activity is NOT converting; pivot, don't repeat.",
        },
        "verdict_breakdown": verdicts,
    }

    OUT.write_text(
        "# Apollo Agent Scorecard (deterministic — what you are evaluated against)\n\n"
        f"_Computed {scorecard['as_of']}. Criteria: APOLLO_SCORECARD.md._\n\n"
        "## PRIMARY — real external outcomes (the only true measure)\n"
        "> WE ARE TESTING WHAT MARKETING CONVERTS INTO STARS. Stars (Codeberg+GitHub) are the dependent\n"
        "> variable of every experiment; sends/opens/replies are INSTRUMENTS for learning what converts —\n"
        "> never the outcome. An experiment that sends flawlessly but moves 0 stars and produces 0 learnings = failed.\n"
        f"- Codeberg stars: **{stars} ({stars_delta})**  ← if delta is +0, your process work is NOT converting.\n"
        f"- GitHub mirror stars (secondary): **{gh_stars} ({gh_stars_delta})** — a star on either surface is a "
        f"real human converting; count BOTH when judging whether a tactic worked.\n"
        f"- VALIDATED customer learnings (real external human + verbatim quote): **{discovery_n}** "
        f"(of {discovery_raw} raw entries — internal/aggregate/synthesized notes and delivery logs "
        f"do NOT count toward the gate)\n"
        f"- Worked tactics (all-time): **{worked}**\n"
        + (f"- **SEQUENCE OUTREACH (Apollo live):** {seq['active']} active seq ({seq.get('ralph_active',seq['active'])} Ralph-AB) · **{seq.get('sent',0)} sent** · "
           f"{seq['opened']} opens [unfiltered, MPP-raw; {seq.get('opened_tracked',0)} tracked] (open rate {seq.get('open_rate_pct_unfiltered',0)}% raw / {seq.get('open_rate_pct_tracked',0)}% tracked) · "
           f"**{seq['replied']} replies** · {seq['bounced']} bounced "
           f"(includes {seq.get('hard_bounced',0)} hard_bounced) (**bounce {seq['bounce_rate_pct']}%**, reply {seq['reply_rate_pct']}%). "
           + (f"🚨 R7 VIOLATION: {seq.get('ralph_active',0)} Ralph-AB sequences active (cap 2) — "
              f"a manual/UI activation bypassed the floor's GUARD F. Activation floor must auto-abort "
              f"the violator next run; do not create new sequences until the cap is restored.\n"
              if seq.get('r7_violation') else "")
           + (f"⚠️ QUALITATIVE PHASE: n={seq.get('sent',0)} total is FAR below the 30/arm needed for any "
              f"rate comparison or 'winning angle' claim — replies are individual learnings; grow the "
              f"verified-contact pipeline + multi-step sequences (EXPERIMENT DESIGN STANDARDS). "
              if seq.get('sent', 0) < 30 else "")
           + ("⚠️ BOUNCE RATE BROKEN (>3% on live motion) — verified-emails-only before sending more.\n"
              if seq['bounce_rate_pct'] > 3 else "deliverability ok.\n")
           + (f"  - 🚨 THEATER FLAG: {seq.get('staged_unsent',0)} verified Ralph-AB sequence(s) staged but "
              f"NEVER ACTIVATED ({seq.get('sent',0)} total sent). Activation floor must fire — staging without "
              f"sending is the #1 failure mode.\n" if seq.get('staged_unsent', 0) > 0 and seq.get('sent', 0) == 0 else "")
           + ("".join(
              f"  - 🚨 ARM UNDERFILLED: **{name}: {n if n is not None else '?'}/30 enrolled** — a "
              f"{n}-contact arm is a PLACEHOLDER, not an experiment. Top it up THIS RUN (duty 4: reveal+verify+"
              f"add_contact_ids, ≤10-16 new/day ramp). Waiting for 'Day-N data' on this n is running no experiment at all.\n"
              for name, n in (seq.get('arm_fill') or []) if n is not None and n < 30))
           if seq else "- Sequence outreach: (live stats unavailable this run)\n") + "\n"
        "## PROCESS — leading indicators you control\n"
        f"- ICP confidence: **{icp_conf}**\n"
        f"- Phase 1→2 gate: **{gate_status}**\n"
        f"- Marketer actions (7d): **{marketer_actions_7d}** · research fetches (7d): **{research_7d}** · drafts: **{drafts_n}**\n"
        f"- worked:flat tactic ratio: **{worked}:{flat}**\n\n"
        "## ANTI-THEATER — keep these from rising\n"
        f"- Failing tactics: **{verdicts.get('failing',0)}**. Never repeat a `failing`/`blocked` tactic.\n"
        f"- If stars delta has been +0 for weeks, MORE drafts/enrichments won't fix it — change the ANGLE/channel.\n\n"
        "## How to improve THIS scorecard next run\n"
        "- Double down on tactics the ledger marks `worked`; kill `failing` ones.\n"
        "- Move a PRIMARY metric (a real reply, a star, a new learning) — not just process counts.\n"
        "- If process is high but PRIMARY is flat, that's the signal to pivot the approach.\n",
        encoding="utf-8")

    print(json.dumps(scorecard, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
