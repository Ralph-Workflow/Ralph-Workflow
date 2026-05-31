# StackOverflow Answer Draft

**Question:** Boss wants us to add more AI to our workflow
**URL:** https://stackoverflow.com/questions/79928220/boss-wants-us-to-add-more-ai-to-our-workflow
**Score:** 3.55
**Answers:** 1

---

For production use, make verification a separate phase with its own inputs and outputs.

Practical structure:

1. **Planner step** defines scope and acceptance criteria.
2. **Execution step** changes code only within that scope.
3. **Verification step** runs tests/build/lint/integration checks and compares the result to the original acceptance criteria.
4. **Review step** packages the evidence: diff, commands run, outputs, and any unresolved risks.

That separation matters because self-verification is weak. If the same loop writes code and grades it, you tend to get optimistic results. A better contract is: no passing verification output, no completion.