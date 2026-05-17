# Reddit Post Log — 2026-05-17 14:27:15

- Account: `Informal-Salt827`
- Thread URL: https://old.reddit.com/r/codex/comments/1tf4l2i/codex_feels_like_a_vibe_coders_dream_after_months/
- Comment URL: https://old.reddit.com/r/codex/comments/1tf4l2i/codex_feels_like_a_vibe_coders_dream_after_months/omanifm/
- Note: Manual post on a fresh r/codex workflow/trust thread with a contextual GitHub mirror link.
- Rank: 1
- Title: Codex Feels Like a Vibe Coder’s Dream After Months of Fighting Claude
- Community: r/codex
- Angle: workflow trust + GitHub mirror link

## Comment body

That line about building an entire workflow around babysitting the model is the real tell.

Once the job is repo-scale, the question stops being "which model feels smartest?" and becomes "what forces a boring finish line?" For me that means: a scoped task, explicit done criteria before it starts, tests/lint during the run, then an independent check plus a morning-after receipt of what changed and what still needs a human decision.

Codex feels better right now for a lot of people because it is more willing to keep pushing the loop. But I still do not want Codex or Claude self-certifying their own work. I want to walk away and come back to a reviewable diff with proof it holds up.

If you are already at the point of separate implementer/reviewer agents, RalphWorkflow is basically the free/open-source version of making that local loop repeatable with the agents you already use on your own machine: https://github.com/Ralph-Workflow/Ralph-Workflow
