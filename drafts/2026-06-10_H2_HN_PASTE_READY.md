# H2 Show HN — one-paste block (operator's manual step)

> **Status:** `PENDING_OWNER` (D46 + D50)
> **Source packet:** `agents/marketing/drafts/2026-06-10_launch_assets_READY.md` §H2
> **Firing window:** **Thursday 2026-06-11 14:00-16:00 UTC** (operator's evening, 16:00-18:00 GMT+2)
> **Why Thursday:** the Tue/Wed/Thu 14-16 UTC window for HN launches. Today (Wed) the
> window has passed (it's 22:08 GMT+2 = 20:08 UTC right now). The D50 binding is
> **exhaustive order: fire H2 this run if in window, OR the next window.** Thursday is
> the next window. **Sunday/Monday are NOT HN windows — stars trickle, not spike, then.**
> **Why this matters:** stars flat 7+ days (Codeberg 12, GitHub 3). Per canon §8, stars
> spike on EVENTS, not trickle. The H2 Show HN is THE event. The cold-email experiment
> is powered-null (V3+V9 61 delivered, 0 replies, 0 stars); HN is the high-leverage
> surface for builders + practitioners.

---

## What the operator does (~90 seconds)

1. Open the headed browser (`bash scripts/ensure_headed_browser.sh` brings up Xvfb+xfwm4+headed
   Chrome on DISPLAY=:99 with the persistent profile at `/home/mistlight/.browser-openclaw`).
2. Navigate to `https://news.ycombinator.com/submit`.
3. Log in to HN by hand (mouse/keyboard, no scripts).
4. Paste the **title** + **URL** below into the HN form. Click submit.
5. **IMMEDIATELY** after the submission lands, post the **first comment** below as a
   self-reply on the same thread (this is HN convention — the first comment from the
   submitter is what the thread is judged on).
6. Walk away. The thread lives on its own; check back at +5d (2026-06-16) for reply rate.

---

## THE TITLE (paste exactly)

```
Show HN: Ralph Workflow – run Claude Code/Codex unattended, overnight, local-first
```

(49 chars, no special characters, no emoji. Per HN: title must be specific + a real claim
the submitter can defend.)

## THE URL (paste exactly)

```
https://github.com/Ralph-Workflow/Ralph-Workflow
```

(Per HN: GitHub URL is fine. Codeberg URL works too but GitHub-native is the convention for
Show HN; the README on GitHub mirrors the canonical Codeberg README + has the asciinema
embed. The HN submitter's account is `mistlight` per the persistent browser profile, so
the URL will resolve to the operator's GitHub view.)

## THE FIRST COMMENT (paste immediately after submission)

```
Builder here. Ralph runs the coding agents you already use (Claude Code, Codex, OpenCode)
against a spec, unattended, on your own machine — you hand off a spec at night and review
tested commits in the morning. The README has an unedited asciinema of a full overnight
run. It's free and open-source (Codeberg primary, GitHub mirror).

The part I most want feedback on: what would make you trust an unattended run enough to
review its output instead of re-doing it?
```

(Per HN: the first comment from the submitter is what the thread is judged on. The voice
is builder/founder, the ask is genuine (it's the actual feedback question I'd want from
the thread), the asciinema link is the load-bearing asset — it shows the system
end-to-end, not a screenshot. ONE codeberg.org link implicit in the README; no signatures,
no persona tag, no "— Ken" sign-off.)

---

## Sibling fallbacks (only if HN is blocked)

If HN login fails or the page is unreachable, the binding order says:
- **/r/LocalLLaMA** and **/r/programming** via the headed-browser Reddit login
  (ken.li156@gmail.com per TOOLS.md). The title and body are the same shape, shortened
  to Reddit-friendly: "Show Reddit: Ralph Workflow — run Claude Code/Codex unattended,
  overnight, local-first." Body: 2-3 sentences + the asciinema embed + the
  repo link.
- **Lobsters is invite-only** — NOT a fallback.
- If both HN and the sibling-Reddits are blocked, the operator can still post the
  thread by hand from their own browser session at a later time. The packet is durable
  — it does not expire.

## What the loop will NOT do without operator consent

- Open HN submission, Reddit fallback posts, or any reply on any of them.
- Star/fork/watch any of the HN commenters' repos.
- Update the HN submission text after it's posted.
- Cross-link the thread to other surfaces (Reddit, Mastodon, X).

## Checkback schedule

- **+5d (2026-06-16):** first checkback. Read the thread, log the comment count +
  upvote count + any substantive reply. If a substantive reply contains a real
  question about Ralph's design, draft a follow-up response (operator's call to post).
- **+3d, +7d, +14d thereafter** (D11 binding): continue monitoring. The thread is
  a long-lived artifact — HN traffic decays over days, not hours.
- **+21d (2026-07-02):** mark H2 `worked` / `no_effect` based on stars delta (Codeberg
  + GitHub) attributable to the thread (best-effort via timestamp comparison + reply
  surface inspection).

---

_Last loop action on this packet: 2026-06-10 22:42 GMT+2 — D50 binding ACTUATED with the
D46-compliant DRAFT form. Source packet from 2026-06-10 03:42 (stale 19h) is consistent
with this one-paste version; this file is the operator-facing block. No public writes
(D46 binding)._
