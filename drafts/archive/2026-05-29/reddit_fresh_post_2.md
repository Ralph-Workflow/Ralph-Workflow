Most of the time the real answer is: review the diff, not the agent.

If I don't look at what actually changed, I'm trusting the tool's confidence rather than its output. The security surface and correctness surface are both wider than the agent usually acknowledges, so the default should be review-first, not trust-first.

What that looks like in practice:
- require a short receipt after each pass: what changed, what passed, what failed, what needs a human decision
- treat the diff as the source of truth, not the agent's summary
- flag anything that touched shared interfaces, auth, or data boundaries as automatically requiring a second set of eyes
- never merge a change you haven't at least skimmed, regardless of how clean the agent's self-report looks

The agents are useful for the implementation work. The review step is where the risk lives, and it doesn't scale by delegating it.
