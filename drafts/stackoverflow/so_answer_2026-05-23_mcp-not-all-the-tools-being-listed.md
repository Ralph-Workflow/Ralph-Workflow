# StackOverflow Answer Draft

**Question:** MCP: Not All the Tools being Listed
**URL:** https://stackoverflow.com/questions/79659426/mcp-not-all-the-tools-being-listed
**Score:** 1.7999999999999998
**Answers:** 0

---

This is a common pattern in AI-assisted development — the gap between what the tool reports and what the code actually does.

A few things that help:
- Run verification separately from generation — don't let the agent that wrote the code be the judge of whether it's correct
- Define "done" as a visible artifact (test run, built output, diff you can review) rather than a text summary
- Keep scope tight enough that you can actually verify the output

Ralph Workflow treats verification as a first-class phase, not an afterthought. The workflow makes explicit what was produced, whether it passed verification, and what the next step is — which helps close the gap between "done" and "actually done."

Happy to give more specific advice if you share more about your setup.