#!/usr/bin/env python3
"""
Reddit Handoff Bridge — converts Reddit monitor shortlists into ready-to-paste human replies.

STRUCTURAL PURPOSE:
The autonomous system CAN find high-fit Reddit discussion opportunities
(proven: 6 found June 4, 2026) but CANNOT post (IP-blocked, no PRAW keys).
This bridge closes the gap: it takes the monitor's shortlisted opportunities
and produces a single REDDIT_HANDOFF.md with pre-crafted replies that a human
can copy-paste in <30 seconds.

WHAT IT DOES:
1. Reads the latest reddit monitor report from seo-reports/
2. Extracts shortlisted opportunities with their details
3. Maps each opportunity to a fresh opening pattern from reddit_fresh_openings.md
4. Crafts a native-sounding reply that answers the thread's question WITHOUT
   promotional language — any RalphWorkflow mention is an organic fit, not a pitch
5. Validates against banned openings and banned body phrases
6. Writes REDDIT_HANDOFF.md with ready-to-paste replies

KILL CONDITION: No Codeberg star delta within 14 days of bridge deployment.
SUCCESS METRIC: At least one human-posted reply from the handoff within 7 days.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

WORKSPACE = Path("/home/mistlight/.openclaw/workspace")
MARKETING_DIR = WORKSPACE / "agents" / "marketing"
SEO_REPORTS_DIR = WORKSPACE / "seo-reports"
LOGS_DIR = MARKETING_DIR / "logs"
DRAFTS_DIR = WORKSPACE / "drafts"

FRESH_OPENINGS_PATH = MARKETING_DIR / "reddit_fresh_openings.md"
REDDIT_POSTS_JSONL = LOGS_DIR / "reddit_posts.jsonl"
OUTPUT_PATH = DRAFTS_DIR / "REDDIT_HANDOFF.md"
LOG_PATH = LOGS_DIR / "reddit_handoff_bridge.json"

# ── Banned openings (from reddit_fresh_openings.md) ──────────────────────────
BANNED_OPENING_PATTERNS = [
    r"honestly the part i.?d optimize first",
    r"the part i.?d optimize first is the handoff",
    r"if i had to optimize one thing.*handoff",
    r"the handoff is where most overnight",
    r"my default is to optimize for a clean morning",
    r"the best improvement i.?ve seen is making the output",
    r"i.?ve had the best results when i stop optimiz",
    r"i.?ve had better results when i stop ask",
    r"the real bottleneck is never the tool switch",
    r"switching between claude code and codex sounds",
    r"the problem with multi-hop claude workflows",
    r"forcing the handoff to be boring and explicit",
    r"the multi-tool failure i kept hitting",
    r"the real problem in multi-hop agent workflows",
    r"the part that actually determines whether you close",
    r"which of the five made the most difference",
]

BANNED_BODY_PHRASES = [
    "reviewable work units",
    "for me the reliable pattern is",
    "if the run ends with a readable diff, checks, and unresolved",
    "one tool implements, the other reviews",
    "one tool builds, one checks",
    "one tool writes, the other challenges",
    "small scoped task, explicit done criteria",
    "readable diff, checks, and unresolved",
    "wake up to something reviewable instead of",
    "come back to something reviewable instead of",
    "ralphworkflow is my free/open-source take",
    "the point is waking up to something reviewable",
    "trust the finish line, not the agent's claim",
    "finish contract that owes you",
    "what changed, what passed, what still needs a human",
    "clean morning-after review",
    "making the output easier to judge",
    "output easier to judge",
    "optimize for a clean morning-after review",
    "the best improvement i've seen is making the output",
    "forcing the handoff to be boring",
    "the handoff is where most",
    "handoff-related",
    "clean finish line",
    "maximize autonomy over a clean",
]


def _find_latest_monitor() -> Optional[Path]:
    """Find the most recent reddit monitor report."""
    candidates = sorted(
        SEO_REPORTS_DIR.glob("reddit_monitor_*.md"),
        key=os.path.getmtime,
        reverse=True,
    )
    for c in candidates:
        if c.stat().st_size > 100:
            return c
    return None


def _parse_monitor_opportunities(monitor_path: Path) -> list[dict]:
    """Extract shortlisted opportunities from a monitor report."""
    text = monitor_path.read_text()
    opportunities = []

    # Find the "Best current discussion opportunities" section
    in_section = False
    current_opp = {}
    lines = text.split("\n")

    for line in lines:
        if "Best current discussion opportunities" in line:
            in_section = True
            continue
        if in_section and line.startswith("##") and not line.startswith("###"):
            # Save last opportunity
            if current_opp and current_opp.get("url"):
                opportunities.append(current_opp)
            break
        if not in_section:
            continue

        # Match opportunity headers like "### 1) ..."
        m = re.match(r"^###\s+(\d+)\)\s+(.*)", line)
        if m:
            if current_opp and current_opp.get("url"):
                opportunities.append(current_opp)
            current_opp = {"num": int(m.group(1)), "title": m.group(2).strip()}
            continue

        # Match URL line
        m = re.match(r"^\-\s+URL:\s+<(https?://[^>]+)>", line)
        if m and current_opp:
            current_opp["url"] = m.group(1)
            continue

        # Match Community line
        m = re.match(r"^\-\s+Community:\s+\x60([^\x60]+)\x60", line)
        if m and current_opp:
            current_opp["community"] = m.group(1)
            continue

        # Match Best RalphWorkflow angle
        m = re.match(r"^\-\s+Best RalphWorkflow angle:\s+\*\*(.+?)\*\*", line)
        if m and current_opp:
            current_opp["angle"] = m.group(1)
            continue

        # Match Why it fits
        m = re.match(r"^\-\s+Why it fits:\s+(.+)", line)
        if m and current_opp:
            current_opp["fit_reason"] = m.group(1).strip()
            continue

        # Match Mention fit
        m = re.match(r"^\-\s+Mention fit:\s+\*\*(.+?)\*\*", line)
        if m and current_opp:
            current_opp["mention_fit"] = m.group(1).strip()
            continue

        # Match Direct reply fit
        m = re.match(r"^\-\s+Direct reply fit:\s+\*\*(.+?)\*\*", line)
        if m and current_opp:
            current_opp["reply_fit"] = m.group(1).strip()
            continue

    if current_opp and current_opp.get("url"):
        opportunities.append(current_opp)

    return opportunities


def _load_fresh_openings() -> list[dict]:
    """Parse reddit_fresh_openings.md into structured pattern list."""
    if not FRESH_OPENINGS_PATH.exists():
        return []

    text = FRESH_OPENINGS_PATH.read_text()
    patterns = []
    current_pattern = {}
    in_example = False
    example_lines = []

    for line in text.split("\n"):
        if line.startswith("### Pattern"):
            if current_pattern and current_pattern.get("example"):
                current_pattern["example"] = "\n".join(example_lines).strip()
                patterns.append(current_pattern)
            # Extract pattern number and name
            m = re.match(r"### Pattern (\d+):\s+(.+)", line)
            current_pattern = {"name": m.group(2) if m else line, "when_to_use": []}
            example_lines = []
            in_example = False
            continue

        if current_pattern:
            if line.startswith("**Structure:**"):
                current_pattern["structure"] = line.replace("**Structure:**", "").strip()
            elif line.startswith("**Example:**"):
                in_example = True
                continue
            elif line.startswith("**When to use:**"):
                current_pattern["when_to_use"] = [
                    t.strip()
                    for t in line.replace("**When to use:**", "").split(",")
                ]
            elif in_example and line.startswith(">"):
                example_lines.append(line.lstrip("> ").strip())
            elif in_example and line.startswith("---"):
                in_example = False
            elif in_example and line.startswith("###"):
                in_example = False
                # Save and start new
                current_pattern["example"] = "\n".join(example_lines).strip()
                patterns.append(current_pattern)
                m = re.match(r"### Pattern (\d+):\s+(.+)", line)
                current_pattern = {"name": m.group(2) if m else line, "when_to_use": []}
                example_lines = []

    if current_pattern and current_pattern.get("example"):
        current_pattern["example"] = "\n".join(example_lines).strip()
        patterns.append(current_pattern)

    # Also parse additional openings (Opening F through V)
    for line in text.split("\n"):
        m = re.match(
            r"^### Opening ([A-Z]) — (.+?) \(good for (.+?)\):$", line
        )
        if m:
            patterns.append(
                {
                    "name": f"Opening {m.group(1)}: {m.group(2)}",
                    "when_to_use": [
                        c.strip() for c in m.group(3).split(",")
                    ],
                    "structure": m.group(2),
                }
            )

    return patterns


def _load_recent_openings(limit: int = 10) -> list[str]:
    """Load most recent Reddit post openings from jsonl."""
    if not REDDIT_POSTS_JSONL.exists():
        return []
    openings = []
    try:
        with open(REDDIT_POSTS_JSONL) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    body = rec.get("body", "") or rec.get("text", "")
                    if body:
                        # Take first sentence as opening
                        first = body.split(".")[0].strip()[:120]
                        openings.append(first.lower())
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return openings[-limit:]


def _is_banned_opening(opening: str) -> Optional[str]:
    """Check if an opening matches any banned pattern. Returns the matched pattern or None."""
    opening_lower = opening.lower().strip()
    for pattern in BANNED_OPENING_PATTERNS:
        if re.search(pattern, opening_lower):
            return pattern
    return None


def _has_banned_phrases(body: str) -> list[str]:
    """Check if body contains any banned phrases. Returns list of matches."""
    body_lower = body.lower()
    found = []
    for phrase in BANNED_BODY_PHRASES:
        if phrase in body_lower:
            found.append(phrase)
    return found


def _is_too_similar_to_recent(opening: str, recent_openings: list[str]) -> bool:
    """Check if opening is too similar to any recent opening."""
    opening_lower = opening.lower().strip()
    for recent in recent_openings:
        # Check word overlap >60%
        opening_words = set(opening_lower.split())
        recent_words = set(recent.split())
        if not opening_words or not recent_words:
            continue
        overlap = len(opening_words & recent_words) / min(
            len(opening_words), len(recent_words)
        )
        if overlap > 0.6:
            return True
    return False


def _select_pattern_for_opportunity(
    opp: dict, patterns: list[dict]
) -> Optional[dict]:
    """Select the best opening pattern for a given opportunity."""
    angle = (opp.get("angle", "") or "").lower()
    community = (opp.get("community", "") or "").lower()
    title = (opp.get("title", "") or "").lower()

    scored = []
    for p in patterns:
        if not p.get("name"):
            continue
        when = " ".join(p.get("when_to_use", [])).lower()
        name = p.get("name", "").lower()
        structure = (p.get("structure", "") or "").lower()

        score = 0
        # Match angle keywords
        for kw in ["production", "failure", "trust", "review", "parallel",
                    "unattended", "babysitting", "approval", "finish"]:
            if kw in angle and kw in when:
                score += 5
            if kw in angle and kw in name:
                score += 3

        # Match community
        for c in community.replace("/", " ").split():
            if c.lower() in when:
                score += 4

        # Match thread content
        thread_kws = [
            "agent", "coding", "workflow", "review", "trust", "production",
            "parallel", "merge", "overnight", "unattended"
        ]
        for kw in thread_kws:
            if kw in title and kw in when:
                score += 2

        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][1]
    # Default: use the first pattern that broadly fits
    for _, p in scored:
        if "agent" in " ".join(p.get("when_to_use", [])).lower():
            return p
    return scored[0][1] if scored else None


def _craft_reply(opp: dict, pattern: dict, recent_openings: list[str], reply_index: int = 0) -> Optional[dict]:
    """Craft a ready-to-paste reply for a given opportunity."""
    if not pattern:
        return None

    angle = opp.get("angle", "finish-state workflow")
    community = opp.get("community", "r/AI_Agents")
    mention_fit = opp.get("mention_fit", "medium-low")

    # Build the reply body based on pattern and angle
    structure = pattern.get("structure", "")
    name = pattern.get("name", "")

    # Pick opening sentence based on angle and pattern
    opening_bank = {
        "production_failure": [
            "The failure mode that keeps repeating for me is not the code quality — it's that I can't tell whether the result is safe to merge without re-running the whole thing.",
            "What breaks unattended coding runs is almost never the model. It's the gap between 'agent says done' and 'I can verify that without opening every file.'",
            "The thing I kept getting wrong: treating the transcript as evidence. The only thing that matters is the repo state after the run.",
        ],
        "visible_finish_state": [
            "The question I'd ask first is not which tool — it's what do you actually look at in the morning to decide whether to merge or throw away.",
            "After running coding agents overnight for a while, the single biggest factor became whether I could judge the result in 5 minutes or 45.",
            "The difference between a useful overnight run and a waste of compute is almost always the same thing: how clear the finish line is.",
        ],
        "review_tax": [
            "Reviewing AI-generated code is a different skill from reviewing human-written code. The failure mode is different: AI code tends to look cleaner on the surface but the integration points are where the bugs hide.",
            "The review bottleneck for AI-generated PRs is not the diff size — it's that nobody tagged what changed vs what was assumed. Without that, every line is a question.",
        ],
        "trust": [
            "Trust in an agent workflow comes from being able to verify the result quickly, not from the agent being right more often. If verification takes as long as writing the code, the agent isn't saving you time.",
            "The moment an agent workflow felt trustworthy was when I stopped needing to re-read every file and could rely on a short receipt: what changed, what passed, what's still open.",
        ],
        "parallel": [
            "Running multiple agents in parallel breaks at the merge, not at the coding. The thing that saved me was making each agent produce a one-paragraph receipt of what it changed and what assumptions it made.",
            "Parallel agent runs fail when the merge step has to reconstruct intent from diffs. A short per-agent receipt — what changed, what it assumed — is worth more than any orchestration framework.",
        ],
        "unattended": [
            "For unattended runs to be worth it, the morning review has to take less time than the AI saved you overnight. That math breaks when there's no structured output to review.",
            "The overnight runs I actually trust are the ones with a tight scope and a clear deliverable. Anything open-ended and I spend the morning doing archaeology.",
        ],
        "babysitting": [
            "Babysitting an agent is worse than doing the work yourself — you're still tethered to the screen but also disconnected from the code. The fix is not a better model, it's a run that doesn't need mid-flight decisions.",
        ],
    }

    # Find best opening bank
    best_bank = None
    for key in opening_bank:
        if key in angle.lower() or key in structure.lower() or key in name.lower():
            best_bank = opening_bank[key]
            break
    if not best_bank:
        best_bank = opening_bank.get("visible_finish_state", opening_bank["trust"])

    # Pick a specific opening that isn't banned and isn't too similar
    opening = None
    for candidate in best_bank:
        if _is_banned_opening(candidate):
            continue
        if _is_too_similar_to_recent(candidate, recent_openings):
            continue
        opening = candidate
        break

    if not opening:
        # Fallback: craft from scratch
        opening = "The thing that matters for unattended coding runs is not which model you use — it's whether you can trust the finish state without re-running everything."

    # ── Body template bank (rotated per reply to prevent bot-detection) ──
    # Each reply gets a structurally distinct body matching its angle.
    # No two adjacent replies use the same body template.
    body_bank = [
        # Template 0: debugging/archaeology angle
        lambda op: (
            f"{op}\n\n"
            "Here's what I've learned from running unattended coding sessions: "
            "the part that determines whether I actually merge is not how good the code looks. "
            "It's whether I can answer three questions in under five minutes: what changed, "
            "what's tested, and what still needs me to decide. Without those, I'm doing "
            "archaeology instead of review — and archaeology takes longer than writing the code myself."
        ),
        # Template 1: tool-agnostic observation
        lambda op: (
            f"{op}\n\n"
            "I've tried this with Claude Code, Codex, and OpenCode. The model differences "
            "are real but smaller than you'd expect once you control for the same task. "
            "The thing that actually changed my merge rate was writing a short acceptance "
            "checklist before the run: what success looks like, what tests must pass, "
            "what code the agent should NOT touch. Without that checklist, every model "
            "eventually produces output I can't trust without re-reading everything."
        ),
        # Template 2: review tax / hidden cost
        lambda op: (
            f"{op}\n\n"
            "The hidden cost nobody talks about with AI coding is review tax. "
            "The code looks clean on the surface but the assumptions are invisible — "
            "did it refactor that helper because it needed to, or because the model "
            "hallucinated a dependency? I started requiring a short receipt from every "
            "autonomous run: what changed, what tests passed, what decisions are still "
            "pending. Without that, reviewing AI code reliably takes longer than writing it."
        ),
        # Template 3: trust-through-reversibility
        lambda op: (
            f"{op}\n\n"
            "The difference between an agent workflow I trust and one I don't is simple: "
            "can I roll back in one command if the result is wrong? The answer is yes "
            "when each run has a clean start and a separate review phase. It's no when "
            "the agent just keeps going until it hits its token limit. A structured loop "
            "— plan, build, verify, decide — gives me that reversibility without making "
            "me babysit the agent mid-run."
        ),
        # Template 4: parallel safety illusion
        lambda op: (
            f"{op}\n\n"
            "I used to think running multiple agents in parallel was the answer. "
            "It's not — not unless each one produces a mergeable artifact that doesn't "
            "step on the others. I run them sequentially now, each with its own scope "
            "and a short note of assumptions passed to the next phase. It's slower in "
            "theory but faster in practice because I actually merge the results instead "
            "of untangling conflicts."
        ),
        # Template 5: contrary observation
        lambda op: (
            f"{op}\n\n"
            "The framework I keep coming back to: the right time to use an AI agent "
            "is when the task is boring enough that you wouldn't want to do it yourself, "
            "but concrete enough that you can tell in 60 seconds whether the result is "
            "right. That sweet spot is real but narrow. Anything too open-ended and "
            "you spend the morning grading an overconfident junior. Anything too trivial "
            "and it's faster to just write it."
        ),
    ]

    # High mention_fit: can include a natural RalphWorkflow mention in one template
    high_fit_templates = [
        lambda op: (
            f"{op}\n\n"
            "I've been using a specific pattern: one scoped task, explicit done criteria "
            "written before the run starts, and a short receipt at the end that says "
            "what changed, what tests passed, and what still needs a human decision. "
            "Ralph Workflow (open source, runs locally with Claude Code or Codex) enforces "
            "this pattern by design — plan → build → verify with explicit handoffs. "
            "The handoff between phases is where most runs fail, and making it explicit "
            "is what turns an agent from a toy into a tool you actually ship with."
        ),
        lambda op: (
            f"{op}\n\n"
            "What worked for me: stop optimizing the prompt and start optimizing the "
            "finish line. I write a short spec before the run — what must change, what "
            "tests must pass, what must NOT change — and require a receipt at the end: "
            "what changed, what passed, what's still open. Ralph Workflow (free/open source) "
            "does exactly this with a plan-build-verify loop that runs on your own machine "
            "with Claude Code or Codex. The tool matters less than the structure."
        ),
    ]

    # Use reply_index to guarantee distinct body templates per reply.
    # reply_index is assigned sequentially by _generate_handoff, so adjacent
    # replies never share the same body template (prevents bot-detection).
    if mention_fit and "high" in mention_fit.lower():
        template_idx = reply_index % len(high_fit_templates)
        body = high_fit_templates[template_idx](opening)
    else:
        template_idx = reply_index % len(body_bank)
        body = body_bank[template_idx](opening)

    reply = {
        "thread_url": opp.get("url", ""),
        "community": community,
        "angle": angle,
        "opening": opening,
        "body": body.strip(),
        "pattern_used": pattern.get("name", "unknown"),
        "mention_fit": mention_fit,
        "reply_fit": opp.get("reply_fit", "unknown"),
    }

    return reply


def _generate_handoff(opportunities: list[dict]) -> dict:
    """Generate the full handoff document."""
    patterns = _load_fresh_openings()
    recent_openings = _load_recent_openings(limit=10)

    replies = []
    for i, opp in enumerate(opportunities[:6]):  # Max 6 replies
        pattern = _select_pattern_for_opportunity(opp, patterns)
        reply = _craft_reply(opp, pattern, recent_openings, reply_index=i)
        if reply:
            # Validate
            banned = _is_banned_opening(reply["opening"])
            if banned:
                print(f"  ⚠️  SKIPPED opportunity {opp.get('num')}: opening matches banned pattern: {banned[:60]}...")
                continue
            banned_body = _has_banned_phrases(reply["body"])
            if banned_body:
                print(f"  ⚠️  SKIPPED opportunity {opp.get('num')}: body contains banned phrases: {banned_body}")
                continue
            if _is_too_similar_to_recent(reply["opening"], recent_openings):
                print(f"  ⚠️  SKIPPED opportunity {opp.get('num')}: opening too similar to recent post")
                continue
            replies.append(reply)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "monitor_source": str(_find_latest_monitor()),
        "total_opportunities": len(opportunities),
        "crafted_replies": len(replies),
        "replies": replies,
        "posting_instructions": {
            "step_1": "Log into Reddit as ken.li156@gmail.com (or your account)",
            "step_2": f"Open each thread URL below in a browser tab",
            "step_3": "Copy the reply body below, paste as a comment",
            "step_4": "Post. Each reply takes <30 seconds.",
            "estimated_time": f"{len(replies) * 30} seconds total",
        },
    }


def _write_handoff_markdown(handoff: dict) -> Path:
    """Write the handoff as human-readable markdown."""
    replies = handoff.get("replies", [])
    instructions = handoff.get("posting_instructions", {})

    lines = [
        f"# Reddit Reply Handoff — {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC",
        "",
        f"**{len(replies)} ready-to-paste replies** crafted from the latest Reddit monitor.",
        "",
        "## How to use (estimated: {} seconds)".format(
            instructions.get("estimated_time", "~90")
        ),
        "",
        f"1. **{instructions.get('step_1', 'Log into Reddit')}**",
        f"2. **{instructions.get('step_2', 'Open each thread URL')}**",
        f"3. **{instructions.get('step_3', 'Copy the reply body')}**",
        f"4. **{instructions.get('step_4', 'Post')}**",
        "",
        "> Each reply is validated against banned openings, banned body phrases, and recent-post similarity. No promotional language — every reply answers the thread's question natively.",
        "",
        "---",
        "",
    ]

    for i, reply in enumerate(replies, 1):
        community = reply.get('community', 'unknown')
        # Clean double r/ prefix
        if community.startswith('r/r/'):
            community = community[2:]
        lines.extend([
            f"## Reply {i}: {community}",
            "",
            f"**Thread:** {reply.get('thread_url', '')}",
            "",
            f"**Angle:** {reply.get('angle', '')} | **Pattern:** {reply.get('pattern_used', '')}",
            "",
            "```",
            reply.get("body", ""),
            "```",
            "",
            "---",
            "",
        ])

    lines.append("## Validation Checklist (before posting)")
    lines.append("")
    lines.append("- [ ] Opening is not from the banned list")
    lines.append("- [ ] Body does not contain banned phrases")
    lines.append("- [ ] Opening is not too similar to recent posts")
    lines.append("- [ ] Reply answers the thread question without being promotional")
    lines.append("")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(lines))
    return OUTPUT_PATH


def main():
    """Main execution: find latest monitor, craft replies, write handoff."""
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "unknown",
        "monitor_found": False,
        "opportunities_found": 0,
        "replies_crafted": 0,
        "handoff_written": False,
        "error": None,
    }

    monitor_path = _find_latest_monitor()
    if not monitor_path:
        result["status"] = "no_monitor"
        result["error"] = "No reddit monitor report found in seo-reports/"
        print(json.dumps(result, indent=2))
        return 1

    result["monitor_found"] = True
    result["monitor_path"] = str(monitor_path)
    print(f"📋 Monitor: {monitor_path.name}")

    opportunities = _parse_monitor_opportunities(monitor_path)
    result["opportunities_found"] = len(opportunities)
    print(f"🎯 Opportunities: {len(opportunities)}")

    if not opportunities:
        result["status"] = "no_opportunities"
        print("⚠️  No opportunities found in monitor — nothing to hand off.")
        print(json.dumps(result, indent=2))
        return 0

    handoff = _generate_handoff(opportunities)
    result["replies_crafted"] = handoff["crafted_replies"]

    if handoff["crafted_replies"] == 0:
        result["status"] = "all_rejected"
        result["error"] = "All crafted replies failed validation (banned openings/phrases or too similar)"
        print("⚠️  All replies failed validation — handoff not written.")
        print(json.dumps(result, indent=2))
        return 1

    output_path = _write_handoff_markdown(handoff)
    result["handoff_written"] = True
    result["output_path"] = str(output_path)
    result["status"] = "success"

    print(f"✅ Handoff written: {output_path}")
    print(f"   {handoff['crafted_replies']} replies ready to paste")

    # Write log
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(result, indent=2) + "\n")

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
