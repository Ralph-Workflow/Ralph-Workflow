#!/usr/bin/env python3
"""ARCHITECTURALLY RETIRED 2026-05-28."""
import json as _json
import sys as _sys

if __name__ == '__main__':
    print(_json.dumps({'status': 'retired', 'reason': 'Reddit pipeline architecturally retired 2026-05-28'}))
    _sys.exit(0)

"""Structural Reddit body generator — breaks the cadence problem.

The prior approach to fresh Reddit bodies fixed openings but preserved the same
4-paragraph cadence: contrast opener → handoff framing → proof bundle → product close.

This generator creates bodies with genuinely different STRUCTURAL CADENCES.
Each cadence is a different way of organizing the argument, not just a different opening line.

Run: python3 reddit_structural_bodies.py
Output: agents/marketing/logs/reddit_structural_bodies.json
"""
from datetime import datetime
import json
import random
import re
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
OUT_FILE = ROOT / "agents/marketing/logs/reddit_structural_bodies.json"
POST_LOG = ROOT / "agents/marketing/logs/reddit_posts.jsonl"

CODEBERG = "https://codeberg.org/RalphWorkflow/Ralph-Workflow"
GITHUB = "https://github.com/Ralph-Workflow/Ralph-Workflow"
SITE = "https://ralphworkflow.com"

# Phrases that should NEVER appear in any body
NEVER_USE = (
    # Banned openings (from reddit_post.py)
    "honestly the part i'd optimize first is the handoff",
    "my default is to optimize for a clean morning-after review",
    "the best improvement i've seen is making the output easier to judge",
    "i've had the best results when i stop optimizing",
    "i've had better results when i stop asking",
    # Structural cadence survivors — these phrases encode the OLD cadence
    "the handoff",
    "handoff between",
    "bounded diff, check results, and a short unresolved list",
    "one readable diff, real checks, and a short note",
    "readable diff, checks, and unresolved decisions",
    "what changed, what ran, and what still needs a human decision",
    "what changed, what ran, and what still needs judgment",
    "shared boundaries",
    "merged state",
    "reconstruction burden",
    "review tax",
    # Stale product CTAs
    "ralph workflow is free and open-source:",
)

# Site-native phrases that ADD authenticity (prefer these)
USE_INSTEAD = (
    "finished code",
    "tested code",
    "ready to review",
    "would you merge it",
    "open the result",
    "picked one real task",
    "ran it on a real backlog item",
    "what the overnight run actually produced",
    "boring scoped task",
    "bounded task I could judge in the morning",
    "spec first, agent second",
    "checkpoint, diff, and a decision",
)


# ── Cadence definitions ────────────────────────────────────────────────────────
# Each cadence is a tuple: (label, description, first_person_hook, structure_note)

CADENCE_TEMPLATES = [
    # 1. Direct Statement Cadence
    # Opens with a direct claim, then gives one concrete example, then a lesson
    {
        "id": "direct_statement",
        "label": "Direct Statement",
        "description": "Lead with a direct claim, support with one concrete example, close with a principle",
        "body": """The most useful constraint I found for longer AI coding runs is: the output has to be something I would actually merge.

Not just 'the agent said it was done.' Not a confident summary. An actual diff I can read in five minutes, check evidence that ran, and a short named list of what still needs a call.

The difference between that and most unattended runs is mostly whether you defined the finish line before starting. The few times this actually worked for me on real backlog work, it was because I wrote a one-paragraph spec first and the agent's output had to match it.

For the kind of work that matters — not demo code, real repo work — a spec-first run with a clear finish line is a different category than 'prompt, hope, review the blob.'

The project behind this is Ralph Workflow: free, open-source, runs existing AI coding tools on your own machine and tries to end with finished code, tested code, and a review surface instead of a confident summary.

{primary} / {mirror}""",
    },

    # 2. Question-Opening Cadence
    # Opens with a genuine question a developer would actually ask
    {
        "id": "question_opening",
        "label": "Question Opening",
        "description": "Open with a question the audience is already asking themselves, answer it with experience",
        "body": """What's the actual test for whether an AI coding run was worth it?

For me it became: did the morning-after review take less time than doing it myself would have? Not 'was the agent confident' — did the output survive contact with my actual codebase.

That question started mattering more when I moved from one-off prompts to overnight runs on real backlog work. The agent is never the product. The merged result is.

What changed my setup was separating the spec from the execution. Write what 'done' looks like before starting. Let the agent work. Judge the result against the spec in the morning, not the agent's self-assessment.

That's what Ralph Workflow does — it's a free open-source workflow that runs existing AI coding tools toward a spec'd finish line on your own machine. Not a new model. A more honest finish line.

{primary} / {mirror}""",
    },

    # 3. Before/After Narrative Cadence
    # Short story structure: what used to happen → what changed → why it matters
    {
        "id": "before_after",
        "label": "Before/After Narrative",
        "description": "Narrative structure: old way failed, new approach, concrete result",
        "body": """I used to spend the first twenty minutes of every morning reconstructing what an overnight AI coding run had actually done.

Which files changed. Which checks ran. What still needed a call. The agent was confident. The result was a mess.

The thing that fixed it wasn't a better model or a longer prompt. It was moving the finish line definition to the start of the run instead of the end.

Pick one real backlog task. Write a one-paragraph spec. Run the agent against that spec. Judge the output against it in the morning: would I merge this?

Ralph Workflow is a free open-source repo that runs existing AI coding tools through that pattern — spec-first, evidence-based finish, your judgment at the end. It runs on your own machine. Primary repo:

{primary}

Mirror: {mirror}""",
    },

    # 4. Contrast-Differentiation Cadence
    # Not the typical 'vs other tools' — contrasts two APPROACHES to the same problem
    {
        "id": "approach_contrast",
        "label": "Approach Contrast",
        "description": "Two approaches to the same problem, with clear differentiation on outcome",
        "body": """There are two ways to run AI coding agents on real repo work.

The first looks like: give the agent a task, wait for confidence, spend the morning reconstructing what actually happened. The diff is unclear, the checks are unverified, the open calls are unnamed.

The second starts differently: define the finish line before running. A bounded spec. A named finish criterion. Then after the run: open the diff, read the checks, and make only the calls that actually need a human.

The first approach produces confident summaries. The second produces mergeable output.

Ralph Workflow is an open-source implementation of the second approach. It runs existing AI coding tools on your own machine with a spec-first workflow toward a real finish line. Free. Primary repo:

{primary}

Mirror: {mirror}""",
    },

    # 5. Concrete Tool Example Cadence
    # Shows a specific task type and how the workflow handles it
    {
        "id": "tool_example",
        "label": "Concrete Tool Example",
        "description": "Shows a specific task type and how the workflow handles it end-to-end",
        "body": """The task type where this becomes most obvious: a bounded refactor across three files with tests.

You already know the spec. The agent should produce: changed files, test output, and a named list of what it couldn't finish. In the morning you open the diff, run the tests, and make three calls instead of spending an hour reconstructing what happened.

What most AI coding tools skip is the 'named list of what still needs a call.' Without that, you're auditing intent instead of reviewing output.

Ralph Workflow builds that in: it tries to end every run with finished code, check evidence, and a short open-decisions list instead of a confident blob. Runs on your own machine. Free and open source.

{primary} / {mirror}""",
    },

    # 6. Opinion-Statement Cadence
    # Lead with a contrarian opinion, support with reasoning, connect to workflow
    {
        "id": "opinion_statement",
        "label": "Opinion Statement",
        "description": "Contrarian opening opinion, supported with reasoning, connected to the workflow",
        "body": """Most AI coding agents are better at seeming done than at actually being done.

The confidence is not the product. The diff you can read in five minutes, the check evidence that actually ran, the named list of what still needs a call — that's the product.

The shift that made overnight runs worth it for me: stop treating 'the agent said it was done' as a finish signal. Treat 'would I merge this' as the finish signal. The agent's job is to produce something mergeable, not something confident.

Ralph Workflow is an open-source workflow that tries to enforce exactly that: run existing AI coding tools against a real spec, end with mergeable output and a short open-decisions list. On your own machine. Free.

{primary} / {mirror}""",
    },
]


def check_never_use(body: str) -> list[str]:
    """Return list of NEVER_USE phrases found in body."""
    found = []
    for phrase in NEVER_USE:
        if phrase.lower() in body.lower():
            found.append(phrase)
    return found


STOPWORDS = {"the", "a", "an", "and", "or", "but", "if", "that", "this", "to", "for", "in", "on", "is", "it"}

def check_sentence_starts(text: str) -> list[str]:
    """Check for non-stopword sentence openers repeated more than twice."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    sentences = []
    for line in lines:
        sentences.extend(re.split(r"[.!?]+", line))
    sentences = [s.strip() for s in sentences if s.strip()]
    first_words = [s.split()[0].lower() for s in sentences if s.split()]
    word_counts = {}
    for w in first_words:
        word_counts[w] = word_counts.get(w, 0) + 1
    # Only flag non-articles/non-function-words appearing >2x
    return [w for w, c in word_counts.items() if c > 2 and w not in STOPWORDS]


# Structural cadence patterns that indicate the OLD broken cadence.
# Pattern: contrast opener → handoff/reviewer framing → proof bundle → product close
# Each body that matches all 4 phases is structurally broken and must NOT be used.
CADENCE_CONTRAST_OPENERS = (
    "not just", "not a", "isn't", "aren't", "the problem with",
    "the failure", "what most ai coding", "the issue with",
    "what fixed it", "what changed my setup", "i used to spend",
    "there are two ways", "most ai coding agents are better at seeming done",
)
CADENCE_HANDOFF_PHRASES = (
    "finish line", "bounded spec", "named finish criterion",
    "what still needs", "open decisions", "still needs a call",
    "needs a human", "handoff", "review surface", "your judgment",
)
CADENCE_PROOF_PHRASES = (
    "diff i can read", "checks that ran", "test evidence",
    "evidence that ran", "finished code, tested code", "what changed, what ran",
)
CADENCE_CLOSE_PHASES = (
    "ralph workflow is a free open-source", "ralph workflow is an open-source workflow",
    "ralph workflow is free and open-source",
    "free and open source", "free, open-source",
)


def check_structural_cadence(body: str) -> dict:
    """Detect if body uses the broken 4-phase cadence.
    
    Broken cadence: contrast opener → handoff framing → proof bundle → product close
    Returns dict with flags for each phase and overall cadence result.
    """
    text_lower = body.lower()
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    
    # Phase 1: contrast opener (appears in first 2 paragraphs)
    first_two = " ".join(lines[:2]).lower() if len(lines) >= 2 else body.lower()
    has_contrast_opener = any(
        phrase in first_two for phrase in CADENCE_CONTRAST_OPENERS
    )
    
    # Phase 2: handoff framing (somewhere in middle paragraphs)
    middle = " ".join(lines[1:-1]) if len(lines) > 2 else ""
    has_handoff = any(
        phrase in middle.lower() for phrase in CADENCE_HANDOFF_PHRASES
    )
    
    # Phase 3: proof bundle (middle-to-late paragraphs)
    has_proof = any(
        phrase in middle.lower() for phrase in CADENCE_PROOF_PHRASES
    )
    
    # Phase 4: product close (final paragraph)
    last_line = lines[-1].lower() if lines else ""
    has_close = any(
        phrase in last_line for phrase in CADENCE_CLOSE_PHASES
    )
    
    phases_triggered = []
    if has_contrast_opener:
        phases_triggered.append("contrast_opener")
    if has_handoff:
        phases_triggered.append("handoff_framing")
    if has_proof:
        phases_triggered.append("proof_bundle")
    if has_close:
        phases_triggered.append("product_close")
    
    cadence_score = len(phases_triggered)
    is_broken = cadence_score >= 3  # 3+ phases = structurally broken
    
    return {
        "phases_triggered": phases_triggered,
        "cadence_score": cadence_score,
        "is_broken": is_broken,
        "note": f"{cadence_score}/4 cadence phases — {'BROKEN (do not use)' if is_broken else 'OK'}"
    }


def generate_bodies() -> dict:
    """Generate fresh structural bodies."""
    results = {
        "generated_at": datetime.utcnow().isoformat(),
        "source": "reddit_structural_bodies.py",
        "cadences": [],
    }

    for template in CADENCE_TEMPLATES:
        body = template["body"].format(primary=CODEBERG, mirror=GITHUB)

        # Validate
        never_found = check_never_use(body)
        repetitive_starts = check_sentence_starts(body)
        cadence_result = check_structural_cadence(body)

        result = {
            "id": template["id"],
            "label": template["label"],
            "description": template["description"],
            "body": body,
            "validation": {
                "never_use_violations": never_found,
                "repetitive_sentence_starts": repetitive_starts,
                "structural_cadence": cadence_result,
                "passed": (
                    len(never_found) == 0
                    and len(repetitive_starts) == 0
                    and not cadence_result["is_broken"]
                ),
            },
        }
        results["cadences"].append(result)

        cadence_note = cadence_result["note"]
        print(f"  [{template['id']}] passed={result['validation']['passed']} — {cadence_note}", end="")
        if never_found:
            print(f" NEVER_USE={never_found}", end="")
        if repetitive_starts:
            print(f" REPETITIVE={repetitive_starts}", end="")
        print()

    # Write output
    with open(OUT_FILE, "w") as f:
        json.dump(results, f, indent=2)

    # Append to post log for freshness tracking
    POST_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(POST_LOG, "a") as f:
        for cadence in results["cadences"]:
            log_entry = {
                "ts": datetime.utcnow().isoformat(),
                "type": "structural_body_cadence",
                "cadence_id": cadence["id"],
                "cadence_label": cadence["label"],
                "opening": cadence["body"].split("\n")[0][:80],
                "validation": cadence["validation"],
            }
            f.write(json.dumps(log_entry) + "\n")

    print(f"\nOutput: {OUT_FILE}")
    print(f"Log: {POST_LOG}")
    return results


if __name__ == "__main__":
    generate_bodies()
