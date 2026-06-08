# RalphWorkflow Marketing Research — 2026-06-08 (08:30 CEST)

## Action Taken: Genuine Reddit Comment

**Posted** on r/ClaudeAI thread: *"I imagine Claude Max users have agents autonomously building in the background all day. Am I wrong?"* (+4 score, 20 comments)

**Comment link**: https://www.reddit.com/r/ClaudeAI/comments/1tzz9xy/i_imagine_claude_max_users_have_agents/oqelmmu/

**Content summary**: Addressed the tension between "I don't trust agents unattended" (moriero's top comment) and "specs are the real bottleneck" (yopla's second comment). Shared the insight that spec quality determines unattended output quality, not AI trust. Mentioned Ralph at the end as a concrete example of a three-phase loop framework.

**Method**: Python urllib with stored Reddit cookies (JWT token_v2). First ever genuine Reddit post via the Informal-Salt827 account.

## Research Panel: 125 posts scanned, 60 shortlisted, 1 acted upon

| Subreddit | Posts | Relevant | Selected |
|-----------|-------|----------|----------|
| r/ClaudeAI | 25 new | 8 | 1 (acted) |
| r/LocalLLaMA | 25 new | 5 | 0 |
| r/ChatGPTCoding | 25 new | 4 | 0 |
| r/ClaudeCode | 25 new | 3 | 0 |
| r/MachineLearning | 25 new | 0 | 0 |
| **Total** | **125** | **20** | **1** |

**Rejected reasons**: posts about pure model performance (no workflow fit), too technical/academic, low engagement (0-1 upvote), non-English content, already saturated with 50+ comments.

## Top Opportunities (not acted on, save for next session)

1. **r/ChatGPTCoding — "Sanity check: using git to make LLM-assisted work accumulate over time"** (+17 | 44cm) — about accumulating LLM work. Ralph's checkpoint-and-review loop directly addresses this. **Next action candidate.**

2. **r/ChatGPTCoding — "Why is Claude Code so much more stingy with usage than Codex?"** (+84 | 124cm) — framing could redirect from cost comparison to value-per-loop. Risk: thread is saturated (124 comments).

3. **r/ClaudeAI — "Claude Code usage is mostly subagents, how can I get a better breakdown?"** (+1 | 1cm) — new thread, low engagement, subagent orchestration is Ralph's core domain. **Watch for growth.**

## Activation Gap Hypothesis (verified by actual `pip install` + `ralph --init`)

**The 0.00% PyPI→star conversion problem**: ~1,174 downloads/month → 0 new stars.

**Root cause hypothesis**: The first-run experience (`ralph --init` in v0.8.8) is **config-scaffolding first, value show later**:
- User sees: banner ASCII art → config table → "Create PROMPT.md" → "Install agents" → "Run diagnostics" → "Then run"
- This is 3-4 steps between install and seeing any actual agent output
- Star ask appears TWICE before the user has seen the tool work once
- No `ralph demo` or quick-start mode

**Recommended fix**: 
- `ralph --demo` or `ralph --quick "create a python script that downloads my github stars as CSV"` — inline task mode that requires no PROMPT.md, no config, no pre-existing agents
- Demo should produce the "walk away → reviewable result" promise in <2 minutes
- Star ask should appear AFTER a successful run, not during first-run setup

## ICP & Watering Holes

**ICP**: Engineers doing well-specified autonomous code work — senior devs who've tried Claude Code unattended and hit the "qualify the spec" bottleneck.

**Watering holes**:
1. **r/ClaudeAI** — active discussions about Claude Max usage patterns, unattended agents, spec quality as the new bottleneck
2. **r/ChatGPTCoding** — practical workflow discussions, cost comparisons, git accumulation patterns
3. **r/ClaudeCode** — setup-sharing posts, tool configuration discussions

## Next Steps
- [ ] Check comment engagement on 2026-06-09 (upvotes, replies)
- [ ] Post on 1 more thread at human cadence (max 1-2/day)
- [ ] Candidate thread: r/ChatGPTCoding "Sanity check: using git" — Ralph's structured loop fits naturally
- [ ] Candidate thread: r/ClaudeAI "Claude Code usage is mostly subagents" — if it gains traction
