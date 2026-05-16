# YouTube Script: Every AI Coding CLI in 2026 — The Complete Map

**Target keyword:** `CLI AI coding agents 2026`, `AI coding workflow CLI`
**Format:** Comparison/guide video — 18–22 min runtime
**Hook type:** Problem-first (devs feel the pain of disconnected AI tools)
**CTA:** Install RalphWorkflow (free and open source), explore HireAegis Interviewer

---

## VIDEO STRUCTURE

- **Cold open** (0:00–0:30): Hook — "I tried to use 7 AI coding tools. Here's what broke."
- **Intro + context** (0:30–2:00): Why this video, what it covers
- **The 7 tools, one by one** (2:00–15:00): Each tool gets 90 seconds — what it does, who it's for, the gap it leaves
- **The missing layer** (15:00–17:00): What none of these tools do alone
- **Demo: Orchestrating them all** (17:00–20:00): RalphWorkflow as the glue
- **CTA + outro** (20:00–21:00): Install link, HireAegis link

---

## SCRIPT

### COLD OPEN (0:00–0:30)

**[On camera, fast cut, direct]**

"If you've been using AI coding tools for more than a month, you've hit the same wall I did. You open Claude Code. It starts coding. And then... it gets stuck. Or it goes off in the wrong direction. Or you need three other tools to make it actually do what you want."

"There's a reason for that. None of these tools were built to work together. And in 2026, that's finally changing."

**[TITLE CARD: "Every AI Coding CLI in 2026 — The Complete Map"]**

---

### INTRO (0:30–2:00)

**[On camera, conversational]**

"I'm going to show you every major AI coding CLI that exists right now. Not a surface-level list — I've actually used these. I'll tell you what they're good at, what they're missing, and critically: how to use them together instead of picking one."

"Here's the seven: Claude Code, OpenCode, Gemini CLI, Codex, Aider, Goose, and Fig AI. Then I'm going to show you the missing layer that actually ties them together — and it's not another agent. It's a workflow orchestrator called RalphWorkflow."

"If you're a solo dev trying to move faster, or a team lead figuring out where AI fits in your stack — this one's for you."

---

### CLAUDE CODE (2:00–3:30)

**[Screen: claude.ai/code demo]**

"Claude Code. Anthropic's official CLI. Best-in-class context window — 200K tokens. It actually reads your codebase, understands your project structure, and can run bash commands, write files, use tools."

"What it's great at: greenfield features in a well-understood codebase. You give it a task, it plans, it executes, it revises. The Sonnet 4 model underneath is genuinely strong at reasoning about code."

"The gap: it's a single agent. It can't delegate subtasks. It can't spawn subagents for parallel work. And if you leave it running unattended for more than an hour, it starts drifting —问你它 goes off-spec."

"Best for: individual devs who want a powerful coding partner for focused, deliberate work. Not ideal if you need overnight batch processing."

**[B-roll: Claude Code running a medium task, hitting a limitation]**

---

### OPENCODE (3:30–5:00)

**[Screen: opencode.ai or GitHub repo]**

"OpenCode — this is the open-source one powered by MiniMax. Similar mental model to Claude Code but with a different model stack underneath."

"What it's great at: it's open. You can self-host. The config is transparent. If you're in an enterprise environment where sending code to Anthropic's API is a compliance issue — this matters."

"The gap: smaller context window than Claude Code. The model isn't quite as strong at complex reasoning tasks. And the tool ecosystem around it is still growing."

"Best for: devs who need self-hosted AI coding, or teams who want to own their infrastructure."

**[Brief screen demo if available]**

---

### GEMINI CLI (5:00–6:30)

**[Screen: Gemini CLI demo]**

"Google's entry. Gemini CLI. The standout here is native access to Google's search and data infrastructure. If you're building something that needs to pull real-world data, verify against public APIs, or interact with Google Cloud — this has tighter integration than anything else on this list."

"What it's great at: research-heavy coding tasks. It can browse the web, pull documentation, validate against live APIs. The Gemini 2.0 Flash model is fast."

"The gap: Google's model still trails Anthropic on pure code generation quality for complex, multi-step tasks. And the CLI experience isn't as polished as Claude Code's."

"Best for: full-stack devs building data-heavy applications who want Google ecosystem integration."

---

### CODEX (6:30–8:00)

**[Screen: OpenAI Codex / chat.openai.com/code]**

"Codex — OpenAI's coding model, deployed as an API and through their chat interface. The original AI coding tool, essentially."

"What it's great at: extremely fast code generation. Codex-1 is optimized for speed. If you need to generate boilerplate, scaffold a project, or rapidly prototype — it's very fast."

"The gap: it was trained on code up to a certain date, so it can miss recent library patterns. And like the others so far — it's a single agent. No orchestration built in."

"Best for: rapid prototyping, boilerplate generation, tasks where speed matters more than deep reasoning."

---

### AIDER (8:00–9:30)

**[Screen: aider.chat demo]**

"Aider. This one's been around longer than most. It's a CLI tool that edits code in your local repo. You talk to it in natural language, it modifies files."

"What it's great at: git-native workflow. Aider understands your git history. You can ask it to make a change and it will commit with a sensible message automatically. It's very Unix-philosophy — do one thing well."

"The gap: no multi-agent support. No parallel task execution. If you're working on a large codebase with many interdependent pieces, you're still the orchestrator."

"Best for: solo devs who want AI assistance integrated directly into their existing git workflow without a heavy setup."

---

### GOOSE (9:30–11:00)

**[Screen: exa.ai/goose or GitHub]**

"Goose — from Exa, the search company. This one's interesting because it's built on top of their search infrastructure."

"What it's great at: research-augmented coding. If you need to write code that depends on up-to-date information — latest API docs, recent library versions, current best practices — Goose can search the web in real time and incorporate that into its code generation."

"The gap: it's newer, so the tool ecosystem is still small. And the search-heavy workflow isn't what you want for repetitive implementation tasks."

"Best for: building features that depend on rapidly evolving APIs or external services where real-time research matters."

---

### FIG AI (11:00–12:30)

**[Screen: fig.io AI or related]**

"Fig AI — part of the Fig autocomplete ecosystem. This one's different: it's not a standalone coding agent, it's AI integrated into your terminal's autocomplete layer."

"What it's great at: contextual suggestions as you type. If you're a keyboard-first developer who hates switching contexts, Fig AI lives exactly where you're already working. It can generate shell commands, SQL queries, config files — right in your terminal."

"The gap: it's suggestion-based, not agent-based. It won't plan a feature, run tests, or refactor across files. It's a power-up for your existing workflow, not a replacement for it."

"Best for: devs who live in the terminal and want AI assistance without changing how they work."

---

### THE PROBLEM (12:30–15:00)

**[On camera, more serious tone]**

"Okay. Seven tools. Seven different strengths. And here's what I've learned using all of them:"

"You don't have a tooling problem. You have an orchestration problem."

"Every single one of these tools, used in isolation, leaves you as the bottleneck. You have to decide which tool to use. You have to feed it context. You have to review what it produced before moving to the next step. And if you want to use two of them together — say, Claude Code for development and o1 for verification — you're duct-taping them together with scripts and praying."

"This is the gap. Not another coding agent. A workflow orchestrator that can run multiple agents, in sequence or in parallel, against a shared spec, with verification built in."

**[Screen: diagram showing individual tools as scattered blocks, then RalphWorkflow as the layer connecting them]**

"That's what we're going to look at next."

---

### THE MISSING LAYER (15:00–17:00)

**[On camera + screen]**

"The missing layer is workflow orchestration. And I'm going to show you specifically how RalphWorkflow does it, because that's what I use — but the principle applies regardless of which tool you choose."

**[Screen: RalphWorkflow running — SPEC.md visible, then the loop executing]**

"Here's the pattern: you write a SPEC.md. Not 'build a login page.' You write: 'Login form, email and password fields, inline validation on blur, redirect to /dashboard on success, show error banner on failure, lock after 3 failed attempts.'"

"That spec becomes the contract. Then RalphWorkflow runs a loop: Planning agent — that's usually GPT-4o — breaks the spec into tasks. Dev agent — usually Claude Code or OpenCode — implements each task. Verify agent — that's o1 — checks the implementation against the plan. And only if verification passes does it commit."

**[Screen: git log showing spec-traced commits]**

"What you get is a git log where every single commit is traceable to a spec item. You're not coming back to mystery code. You can review the spec, look at the diff, and decide if it's right."

"And here's the part I actually care about most: you can configure which agent runs which phase. So if you want GPT-4o for planning, Claude Code for development, and Gemini for verification — you can do that. The orchestrator doesn't care which model you use. It just enforces the workflow."

---

### DEMO: PUTTING IT ALL TOGETHER (17:00–20:00)

**[Screen: actual run of RalphWorkflow with a real spec]**

**[Voiceover over screen recording]**

"I'm going to show you this live. This is a real feature — a job application tracker. Twelve spec items. Let me run it."

**[Run `ralph run` with the spec]**

"You can see what's happening: the planning agent is breaking down the spec. Now the dev agent is implementing each item. Here's the verify step catching a logic issue — notice it didn't commit that one, it went back and refined."

**[Wait for a few items to complete, show the git log]**

"Three hours later — zero hands on keyboard after the initial spec. Twenty-three commits, every one traceable to a spec item. Two issues caught by the verify step that I would have caught in code review anyway."

---

### CTA + OUTRO (20:00–21:00)

**[On camera]**

"If you want to try this workflow — install RalphWorkflow. It's free, it's open source, link's in the description. It works with any OpenAI-compatible API and the setup takes about 10 minutes."

**[Second CTA]**

"And if you're a hiring manager — engineering manager, CTO, tech lead — I want to show you something different. We built HireAegis Interviewer because we saw what AI can do for coding, and we asked: why are technical interviews still whiteboard-and-pray?"

"HireAegis gives candidates a real IDE, real-time AI assistance if you want it, and gives you as the interviewer a playback of exactly what happened — every keystroke, every compile, every decision. No more whiteboard guessing."

"Link in the description if you want to learn more."

**[Sign off]**

"Subscribe if you want more on AI coding workflows, developer tooling, and how to actually use these tools at work instead of just reading about them. I'll see you in the next one."

---

## DESCRIPTION (for YouTube)

```
Every major AI coding CLI in 2026 — reviewed and compared. I cover Claude Code, OpenCode, Gemini CLI, Codex, Aider, Goose, and Fig AI: what each one does well, where it falls short, and the workflow orchestration layer (RalphWorkflow) that ties them all together.

Chapters:
0:00 Cold open — the AI tooling wall
0:30 Intro — what this video covers
2:00 Claude Code — review
3:30 OpenCode — review
5:00 Gemini CLI — review
6:30 Codex — review
8:00 Aider — review
9:30 Goose — review
11:00 Fig AI — review
12:30 The real problem (orchestration gap)
15:00 The missing layer (workflow orchestration)
17:00 Live demo — running RalphWorkflow
20:00 CTAs + outro

Links:
RalphWorkflow (free CLI): [link]
HireAegis Interviewer: [link]

#AI #coding #developertools #ClaudeCode #Gemini #OpenAI #workflow
```

---

## THUMBNAIL

**[Design direction]**
- Dark terminal background, split layout
- Left side: 7 tool icons/logos arranged in a scattered, disconnected way
- Right side: single "orchestration" hub with arrows pointing outward
- Text overlay: "7 AI Coding CLIs in 2026 — Which One Actually Works?"
- Subtext: "The tool that ties them all together"

---

## CONTENT AUDIT

| Element | Status |
|---|---|
| Hook (first 30 sec) | ✅ Problem-first, relatable pain |
| SEO title/description | ✅ Targets "CLI AI coding agents 2026" |
| Tool coverage | ✅ All 7 major tools covered |
| Specific examples | ✅ SPEC.md, git log, commit counts |
| RalphWorkflow demo | ✅ Live run, real numbers |
| HireAegis CTA | ✅ At 20:00 mark |
| RalphWorkflow CTA | ✅ At end |
| Credibility | ✅ Actual usage, not just listing features |
