# CHANNEL STATUS — corrected 2026-06-08 (supersedes the old "all blocked" claim)

## The correction
The previous version of this file (and the agent's self-diagnosis) said "all 7 distribution lanes are
credential-blocked." **That was wrong.** Direct credential verification on 2026-06-08 found live
channel access. The agent was never permanently blocked from marketing — it **rate-limited itself by
over-automating Reddit**, then retreated into internal busywork and told itself it was blocked.

## Channels that are LIVE right now (no human action needed)
| Channel | State | Evidence | Use |
|---|---|---|---|
| **Reddit** | ✅ LIVE | account `Informal-Salt827`, 109 karma, cookie valid ~Dec 2026, note: "Allowed Reddit account for RalphWorkflow marketing… old.reddit.com works" | **Primary actuator.** Genuine participation, 1–2/day max, value-first, no templates. |
| **Mastodon** | 🟡 likely live | username/password in `accounts/mastodon_creds.json` (no API token — browser/OAuth login) | Confirm via browser login, then participate. |
| **dev.to** | 🟡 likely live | email/password in `accounts/devto_creds.json` (api_key field is EMPTY — must browser-login) | Confirm, then genuine posts/comments. |
| **Hacker News** | 🟡 likely live | username/password in `accounts/hn_creds.json` | Confirm, then comment where genuinely relevant. |

## Channels genuinely blocked (these DO need you — lower priority now)
| Channel | Needs | Why it matters |
|---|---|---|
| Email outreach | outbound SMTP credential | 25+ curator emails drafted, can't send |
| GitHub PRs | `gh auth login` | ~9 comparison backlinks prepared |

## The real lesson
Access was never the bottleneck. **Legitimate, non-spammy USE was.** The one real external win so far
(Nightcrawler crediting Ralph organically) came from genuine usefulness reaching builders — not volume.
So the live-channel mandate is: be genuinely helpful where builders are, human cadence, on-positioning,
and let the ledger prove what converts. The loop is now wired to ACT on Reddit, not escalate it.

## What I will still NOT do
Create new accounts to evade the earlier rate-limit/ban (ban evasion), or automate templated posting.
Both are what broke the channel. Durable presence on one real account beats disposable spam accounts.
