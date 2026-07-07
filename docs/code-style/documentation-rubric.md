# Documentation Rubric

This page is the documentation rubric the project applies to every reader-facing docs change.
This is the canonical rubric for Ralph Workflow documentation.

Every docs-related change must refer to this file.
That includes README work, START_HERE work, docs-map changes, manual changes, proof/example pages, contributor-facing docs that shape user-facing behavior, and any docs watchdog or remediation loop.

## What this rubric protects

Ralph Workflow docs must consistently explain the product as:

- **the operating system for autonomous coding**
- a free and open-source **AI agent orchestrator**
- built on a **simple Ralph-loop core**
- **powerful in composition** because the simple core scales into larger workflows
- shipping with a **strong default workflow for writing software**
- something users can adopt **as-is first** and extend later

## Non-negotiable system rules

All public docs must work as one coherent system.

1. `README.md` says what Ralph Workflow is, who it is for, who it is not for, and what the next honest click is.
2. `START_HERE.md` gives the fastest serious first-run path.
3. `docs/README.md` acts as a real map, not another README clone.
4. The Sphinx/manual path behaves like a maintained manual, not a page pile.
5. Top-level routes must stay curated, short, and low-anxiety.
6. Proof/example pages stay secondary to onboarding, how-to, reference, and explanation.
7. Common user questions must be directly answerable from the docs journey, especially operator/config questions.
8. Docs must reflect the real maintained product/runtime, not stale historical framing.

## Minimum structure required by page family

Every documentation page must have a clear page role and the minimum structure that fits that role.

### 1. README

Minimum structure:

- what it is
- who it is for / not for
- shortest honest next step
- install or evaluation entry
- curated route onward

README must not become a manual, a proof dump, or a link farm.

### 2. START_HERE

Minimum structure:

- what kind of evaluator this path is for
- one realistic first-run goal
- prerequisites
- exact first steps
- what success looks like
- where to go next

### 3. Docs map / docs home

Minimum structure:

- short explanation of what the page is for
- route-by-question or route-by-job grouping
- clear separation of tutorials / how-to / reference / explanation / proof / internals
- obvious next click for each route

### 4. Tutorial / guided first-run page

Minimum structure:

- goal
- prerequisites
- exact steps in order
- expected result / success check
- next step

### 5. How-to guide

Minimum structure:

- the exact task/question it answers
- prerequisites or assumptions
- exact steps
- expected outcome / verification
- links to deeper reference only where needed

### 6. Reference page

Minimum structure:

- scope of the reference
- complete factual behavior, fields, commands, or options
- defaults / constraints / important caveats
- examples when they reduce ambiguity

Reference must not be the only place a common task is documented.

### 7. Explanation / concept page

Minimum structure:

- problem or concept being explained
- mental model
- tradeoffs / why it is shaped this way
- links to practical pages for action

### 8. Proof / example page

Minimum structure:

- what this page proves
- what it does **not** prove
- how to interpret the example
- where to go next if the reader wants to try it

Proof pages must not become the main product pitch.

## Required quality criteria

Judge all docs changes against all of these at once:

1. positioning alignment
2. factual / product accuracy
3. clear audience fit and non-fit
4. direct answerability for common user questions
5. direct routing for config/operator tasks such as editing `ralph-workflow.toml`
6. advanced configuration coverage when `pipeline.toml`, `artifacts.toml`, `mcp.toml`, or equivalent surfaces are real user-facing customization layers
7. explicit page-role separation
8. strong README -> START_HERE -> docs map -> manual coherence
9. low duplication across top-level surfaces
10. natural, trustworthy prose
11. low link anxiety
12. obvious next steps
13. promoted pages that reinforce the same story instead of fighting it
14. maintained-manual behavior rather than content-sprawl behavior

## Hard failures

Fail the docs system if any of these are true:

- top-level/public docs lead with artifacts, transcript-vs-diff framing, merge-decision framing, handoff plumbing, or other internal process residue
- reviewable-output framing dominates the product story
- README, START_HERE, docs map, and manual fight each other or duplicate each other without clear role separation
- a common task is only answerable by wandering through many pages
- top-level surfaces technically contain links but still feel cluttered, repetitive, or hard to trust
- docs route users into stale runtime/product framing
- proof/example pages outweigh tutorial/how-to/reference routing in the first-run path
- a page has no clear role, no clear audience, or no obvious next step
- a page family is missing its minimum structure
- public docs sound like process receipts instead of product explanation
- a docs change adds surface area without improving the route
- the user would reasonably need to remind the docs agent of the same rubric again

## Required review note for meaningful docs changes

Meaningful docs changes must leave behind a short review note covering:

- what changed
- why it belongs on that surface
- what was pruned, merged, or explicitly left alone
- how duplication was reduced or contained
- how the route is clearer now than before

## Completion bar

Docs work is not done unless:

- the changed page satisfies the correct page-family minimum structure
- the surrounding route still makes sense as a system
- promoted links resolve to the right runtime/product story
- the next click is clearer, not noisier
- the change improves the docs system instead of merely adding content
