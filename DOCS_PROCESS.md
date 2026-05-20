# Docs Process

This is the canonical operating procedure for Ralph Workflow public docs work.

## Goal
Keep the public docs system clear, persuasive, organized, and easy to understand.

This process exists to stop additive drift: too many links, too much README text, weak information hierarchy, duplication, under-edited copy, and top-level docs that do not make sense together as one user journey.

## Surface Roles

### README.md
Job:
- explain what the project is
- help a new visitor decide if it is for them
- provide a quick proof / trust signal
- show a short quick-start path
- point to the next 3-5 best places

Must not become:
- a manual
- a giant FAQ
- a docs index
- a decision tree of dozens of links

### START_HERE.md
Job:
- guide a serious evaluator through the first real run
- answer the first practical questions in order
- reduce fear and ambiguity

### docs/README.md
Job:
- organize the documentation set
- group pages by user intent
- act as the map that README should not become

### Deep doc pages
Job:
- answer one concrete question well
- reduce complexity at the top level by absorbing detail

## Hard Constraints
- Keep `README.md` at a reasonable length.
- Favor first-screen clarity over completeness.
- Limit top-level link sprawl.
- Prefer pruning and consolidation over constant addition.
- Do not solve discoverability problems by endlessly adding more README text or more README links.
- Do not split a larger docs change into smaller edits to avoid this review process.
- If detail can live one layer down without harming first-run clarity, move it down.

## Full Review Trigger
Full docs review is mandatory for:
- any change to `README.md`
- any change to `START_HERE.md`
- any change to `docs/README.md`
- any docs change that adds, removes, renames, or substantially repurposes a page
- any docs change that adds a top-level link or section
- any docs change that changes navigation, recommended user flow, public positioning, comparison framing, trust/proof surfaces, or quick-start paths

## Full-House Audit Trigger
Run a **full-house docs audit** when the docs system appears to be in a bad state, especially when you notice any of these:
- `README.md` feels too long, cluttered, or hard to scan
- top-level surfaces are behaving like link farms
- new pages are being added faster than old material is being pruned
- README / START_HERE / docs index are duplicating each other
- the information hierarchy feels muddy or inconsistent
- public docs edits have accumulated without a recent holistic pass

A full-house audit is broader than a normal docs edit review. It must examine the whole top-level system together and may conclude that multiple pages need pruning, merging, rerouting, or role changes.

When the audit itself is about **process/governance quality**, use the findings to strengthen the process rules first. Do not drift into public-doc cleanup before the new governance is codified.

## Required Pre-Edit Brief
Before meaningful public-docs edits, write a short Docs Change Brief in your working notes, session note, commit message draft, or delivery summary.

Include:
1. **User/problem addressed**
2. **Target audience**
3. **Chosen surface** (`README`, `START_HERE`, docs index, or deep page)
4. **Why this surface, not the others**
5. **What will be shortened / removed / merged elsewhere**

If you cannot answer those clearly, do not add the content yet.

## Surface Ownership Check
For every new paragraph, section, link, or page, answer:
- Why is this not README material?
- Why is this not START_HERE material?
- Why is this not just a docs-index pointer?
- Can an existing deep page absorb it?
- If this is a new page, does it answer exactly one concrete question?

## One-In / One-Out Rule
Any new top-level doc link, README section, START_HERE section, docs-index group, or deep page must be paired with an explicit keep / replace / remove decision for existing content.

If nothing was removed, merged, or shortened, explain why not.

## Required Review Loop
Review the user journey in order:
1. `README.md`
2. `START_HERE.md` (if present)
3. `docs/README.md` or equivalent docs index (if present)

Do not review them as isolated files only. Review them as a sequence a real visitor would encounter.

If this repo does **not** have `START_HERE.md` and/or `docs/README.md`, do not keep writing process language as if they already exist. Explicitly choose the real top-level architecture:
- `README -> selected deep docs`, or
- `README -> START_HERE -> docs index -> deep docs`

A repo may use the simpler model, but the governance, README routing, and watchdog reports must all describe the same model.

For a **full-house audit**, also inspect the adjacent high-traffic deep pages that the top-level surfaces currently route people toward, so you can judge whether the routing itself is clean or whether the top level is compensating for weak downstream organization.

Ask:
- What does each surface do?
- What is duplicated?
- What feels bloated?
- What would a new visitor actually read?
- Which links are essential, and which are anxiety-driven additions?
- Is one page compensating for another page’s weakness?
- Does each surface still have a distinct job?

## Duplication Pass
List repeated promises, explanations, and links across:
- `README.md`
- `START_HERE.md`
- `docs/README.md`

For each repeated concept:
- choose a canonical location
- shrink the others to short pointers if they still need to exist

Do not repeat the same explanation across top-level surfaces unless the shorter version is necessary for orientation.

## Top-Level Link Budget
README should point only to the next few essential destinations.

Default budget:
- aim for **3-5 primary next-click links** near the top-level path
- if a routing need requires more than that, push the rest into `docs/README.md` or another index surface

Large "see X if Y" link farms are a process failure.

Just as importantly: every promoted README/doc-index link must resolve to a page that actually exists in the repo or at the published target. Routing to missing pages is a ship-blocking information-architecture failure, not a minor cleanup item.

If a promoted link intentionally points to an external canonical docs surface, verify the published target itself. Do **not** mark it broken just because the file is absent from the local checkout. Use the real rendered or raw published path and record which target you verified.

## Continuity Check: Same Product, Same Reality
Promoted next-click pages must feel like the same product and the same documentation system.

Treat these as ship blockers unless the handoff is explicit and intentional:
- README describes one implementation/runtime/package, but the next-click docs describe another
- install instructions, file paths, commands, or architecture claims switch language/runtime without warning
- a linked page identifies itself as historical, mixed-state, or migration-only, but README promotes it like current primary guidance

Do not accept "the link works" as sufficient. The destination must also match the user expectation created by the source page.

If a continuity blocker cannot be fixed in the destination during the same change, the source surface must stop silently routing into it. Either:
- remove/demote the promoted link, or
- add an explicit inline warning that tells the reader what is mismatched and what to trust instead

For mixed-state public docs, do not keep page-level README bullets as a compromise. If the destination still describes the wrong product/runtime/version, remove those page links from the first-run path entirely and route readers to the current canonical surface instead (for example: the local README, `START_HERE.md`, `docs/README.md`, or verified CLI help).

Do not leave a top-level link looking primary when it is only conditionally trustworthy.

## Visitor Test
Simulate a new visitor with 10-15 seconds of attention.

They should be able to answer:
- What is this?
- Is it for me?
- Why is it credible?
- What should I click first?
- If I click through, will the next page feel like the obvious continuation rather than a jarring branch?

If they instead face a wall of links, too many branches, too much explanation, or a confusing jump into the next page, the top-level docs failed.

## Required Copy-Edit Pass
Before shipping, re-read changed public docs for:
- brevity
- rhythm
- heading quality
- repetition
- scanability
- awkward phrasing
- over-explaining
- whether a first-time reader can understand the point without rereading

Do not ship placeholder-good-enough public copy.

If the copy is technically correct but still feels harder to understand than it should be, rewrite it.

## Required Post-Edit Scorecard
A meaningful docs change is incomplete unless you can answer these in writing:
- README shorter / same / longer?
- top-level links fewer / same / more?
- what was removed, merged, or shortened?
- was duplication reduced?
- is the first-click path clearer?
- does each top-level surface still have a distinct role?
- what specifically improved in the bigger picture?
- do README and its linked docs now make more sense as one coherent journey?
- did every promoted link/path get verified to exist and feel like the right next click?
- what still feels hardest to understand, if anything?

Also record, when relevant:
- whether promoted external links were verified at local paths or published targets
- whether the clicked pages still describe the same product/version/runtime the README promised

For a **full-house audit**, also answer:
- what top-level surfaces were in the worst shape?
- what systemic pattern caused the drift?
- what was pruned or reorganized to stop recurrence?
- why is the docs system healthier now, not just locally patched?
- which linked pages were most important to the top-level journey, and do they now feel like the right next step?

## Verification Loop
Do not assume one pass is enough.

After any meaningful docs/process change:
1. inspect the actual resulting surfaces or rules again
2. compare them against the stated criteria
3. identify what still violates the standard
4. get parallel third-party-agent verification of the new result when the change affects process/governance/watchdogs/enforcement loops
5. if any verifier fails, automatically run another remediation pass and then a fresh independent verifier
6. do not let the loop self-certify its own success state
7. iterate until the result passes or you can name the remaining blocker precisely

For docs/process work, a first patch that improves the rules but still leaves the same failure mode possible is not done.

For public-doc cleanup, a first patch that still leaves obvious link-farm behavior, duplication, weak hierarchy, bad scanability, confusing routing, or hard-to-understand copy is not done.

Docs work is not complete until the README and the documentation it routes into feel coherent, well organized, easy to follow, and properly copyedited as a single system rather than a pile of individually useful pages.

Process/watchdog work is not complete until parallel third-party verification agrees the loop is real, visible to other agents, and strong enough to catch regressions.

For any self-improvement loop, third-party verification is required at every claimed improvement state. If a verifier does not sign off, the loop must automatically spin another remediation pass and then another fresh independent verifier instead of declaring success.

No self-improvement/watchdog loop is considered structurally valid unless it also has a registry entry in `agents/system/self_improvement_loops.json` and passes the recurring integrity audit in `agents/system/loop_integrity_audit.py`.

## Ship-Blocking Rule
Do not ship docs changes if the result increases:
- clutter
- duplication
- top-level link sprawl
- navigation anxiety
- README length without a very strong reason
- top-level routing to missing, stale, or misleading pages

Even if the new content is individually good, it should not ship until the top-level experience is better or at least no worse.

Also do not ship when top-level routing crosses into mismatched product/version/runtime docs without an explicit, well-signposted explanation.

## Default Bias
When unsure:
- shorten
- simplify
- consolidate
- prune
- move detail down a layer
- keep README cleaner than feels necessary
