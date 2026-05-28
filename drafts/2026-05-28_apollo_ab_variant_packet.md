# Apollo A/B Message Variant — Anti-Spam Repair
Generated: 2026-05-28T21:48 CEST
Trigger: 0.14% reply rate + 193 spam-blocked on live sequence (758 contacts)

## Why the current body is failing
The current subject line "when an AI coding run actually becomes reviewable" sounds like marketing copy.
The body opens with an abstract claim ("The useful bar for autonomous coding is not...") before identifying who is speaking.
Apollo sequences with subject-first abstract claims + no personal context + no unsubscribe path get spam-flagged.
193 spam-blocked out of ~1000 delivered is a 19% spam rate — this is actively destroying sender reputation.

## A/B Variant — "Morning-after review" (personal, concrete, shorter)

### Subject (3 variants to A/B test)
A: "quick question about your autonomous coding setup"
B: "what happens after your agents finish a run"
C: "the morning-after review problem"

### Body
```
Hi {{first_name}},

I'm building a free open-source tool for autonomous coding workflows, and I'm
talking to engineering teams about what actually happens the morning after an
agent run finishes.

Specifically: when you come back to a big overnight run, do you find something
you can actually review and merge, or do you spend the first hour piecing
together what happened?

That's the problem Ralph Workflow targets — it structures unattended coding
runs so they end in finished, tested, mergeable PRs instead of messy state.

Would love to hear if that resonates. The repo is at:
https://codeberg.org/RalphWorkflow/Ralph-Workflow

If this isn't relevant to your work, no worries — I won't follow up again.

- [Your name]
```

### Anti-spam changes vs current body
1. **Subject is a question** — questions get higher open rates and lower spam scores
2. **Opens with personal framing** ("I'm building..." not "The useful bar for...")
3. **One clear question** not abstract claims
4. **Explicit opt-out** ("I won't follow up again") — signals legitimacy
5. **Shorter body** — 8 lines vs current 12; less text = fewer spam-keyword matches
6. **No bullet points** — bullet-heavy cold emails trigger promotional filters
7. **No feature list** — current body lists features; variant asks about their problem

### If reply rate stays below 1% after A/B
The problem is targeting, not body. Current ICP (founder/CTO/eng mgr) may be too broad.
Switch to narrower ICP: developers who have mentioned "Aider" or "Claude Code" in their
Apollo profile or company description.

### Measurement contract
- A/B variant expected to improve reply rate from 0.14% to 1%+
- Spam-block rate expected to drop from 19% to under 10%
- If neither improves by June 8, switch targeting before touching body again
