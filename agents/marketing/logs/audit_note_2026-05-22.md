# Marketing Workflow Audit Note — 2026-05-22

## Adoption snapshot
| Platform | Stars | Watchers | Forks | Window delta |
|----------|-------|----------|-------|--------------|
| Codeberg (primary) | 10 | 2 | 2 | +0 across 9 samples |
| GitHub (mirror) | 0 | 2 | 0 | +0 across 9 samples |

Signal: first Codeberg star delta (9→10, May 21) — treat as directional, not confirmed causal.

## What worked
- Proof-link/entry-path repairs correlate with first measurable Codeberg signal
- Infrastructure stack complete and holding
- **Reddit structural cadence fix: formally integrated** — `reddit_structural_bodies.py` output (6 validated cadences) is now the primary body source in `reddit_next_window_packet.py`. Fallback to static drafts only when structural output is absent or stale.

## What failed
- **HN submission blocked**: browserless returned HTTP 400 Bad Request; no HN credentials available. Remains the single highest-leverage unmade move. `drafts/HN_LOBSTERS_ACTIVE_PACKET.md` is current and ready for human execution.
- **Backlink indexing velocity: 0 indexed**. Only SaaSHub (1/6 directories) is live.
- Codeberg + GitHub: flat across full measurement window

## What's repetitive
- Every audit cycle: "stay quiet, HN/Lobsters is the move" — correctly identified but cannot be broken through autonomously
- Monitor passes during cooldown — correctly zero-output, correctly cautious

## What's low leverage
- More owned-surface polish (at SEO ceiling)
- More keyword-gap Telegraph posts (at 100% coverage)
- More monitor passes without a new distribution channel

## Repair actions executed
1. **`reddit_next_window_packet.py` structural bodies integration** — `load_structural_bodies()` and `cadence_for_opportunity()` added. Drafting flow now tries structurally verified cadences first (all 6 pass NEVER_USE + repetition validation), falls back to static drafts only when structural output is absent or stale.

## Hard blockers requiring human action
1. **HN/Lobsters submission** — autonomous attempt failed (browserless 400). Credentials or manual submission required. Packet: `drafts/HN_LOBSTERS_ACTIVE_PACKET.md`
2. **Reddit posting** — human-executed using drafted bodies; structural fix is in place
3. **Directory backlinks** — confirm AIToolsIndex, ToolShelf, ToolWise, MadeWithStack, DevToolCenter submissions and listing status

## Execution ceiling verdict
The marketing system has reached its autonomous execution ceiling. All reproducible paths are complete, in-flight, or blocked. The architecture is sound — the bottleneck is the absence of a viable autonomous path for the highest-leverage remaining move (HN submission). Resolving the HN blocker is the only change that can materially improve Codeberg adoption odds from this environment.

## Measurement window
- 14 days through **2026-06-04** for Codeberg stars/watchers/forks delta
- 7 days for directory listing confirmation signals
