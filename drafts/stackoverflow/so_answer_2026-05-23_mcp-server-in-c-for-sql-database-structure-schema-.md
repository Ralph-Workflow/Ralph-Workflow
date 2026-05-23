# StackOverflow Answer Draft

**Question:** MCP server in C# for SQL database structure/schema (in Visual Studio) for Github Copilot
**URL:** https://stackoverflow.com/questions/79856670/mcp-server-in-c-for-sql-database-structure-schema-in-visual-studio-for-github
**Score:** 2.65
**Answers:** 0

---

The most reliable verification is independent of the agent that produced the work. When the same system that writes code also judges whether it's correct, the bar tends to drift.

Practical approach:

**Run verification as a separate step.**
After the main development pass, do a second review pass that runs the tests, checks the output, and judges the result against the original acceptance criteria — without the development agent involved in that judgment.

**Make the verification criteria explicit.**
The spec that guided the development should also guide the verification. If verification fails, you have a concrete failure mode to report back to the development step.

**Don't let "done" be a claim — make it a state.**
The last line of output is not proof of completion. A test suite passing, a built artifact you can inspect, a diff you can review — those are proof.

For autonomous coding workflows, Ralph Workflow makes this explicit: each phase has a defined finish state, and the next phase doesn't start until the previous one has a visible, verifiable result. It's not about more oversight — it's about trustworthy completion signals.

What kind of output are you reviewing — unit tests, integration tests, or the code itself?

---

*Ralph Workflow* is a free and open-source composable loop framework for autonomous coding. It treats verification as a separate phase with a defined output — so "done" means something you can actually inspect. Primary repo: [Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow).
