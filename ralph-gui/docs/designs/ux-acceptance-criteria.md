# Ralph Workflow - UX Principles & Ongoing Quality Standards

UX is not a phase. It's not something you "do" and then you're done. It is a
continuous discipline that shapes every design decision, every new feature, every
iteration of this product. This document is a **living reference** — not a punch
list to complete and file away.

These principles are derived from 10 foundational UX sources and applied
specifically to Ralph Workflow. They should be consulted:

- **Before designing** any new screen or feature
- **During review** of any UI change, no matter how small
- **After shipping** when observing how real usage aligns with expectations
- **Periodically** to re-evaluate existing screens as the product evolves

The goal is not to check every box once. The goal is to internalize these
principles so deeply that they become the default way we think about every
design decision.

---

## How to Use This Document

**When designing something new:** Read the relevant principle sections before
sketching. Let them shape the design from the start — retrofitting good UX is
10x harder than building it in.

**When reviewing a change:** Use the specific criteria as a lens. Does this
change make the experience better, worse, or neutral for each applicable
principle? A feature that adds functionality but violates UX-1 (self-evidence)
or UX-6 (cognitive load) is not a net improvement.

**When something feels "off" but you can't articulate why:** Walk through
the principles systematically. The language here gives you vocabulary to
identify and communicate UX problems precisely.

**When prioritizing work:** Not all principles carry equal weight at all times.
The priority guidance at the end reflects what matters most for Ralph Workflow
specifically, but priorities shift as the product matures. A polished app with
poor self-evidence is worse than a rough app that's immediately understandable.

---

## Sources

1. *Don't Make Me Think* — Steve Krug
2. *The Design of Everyday Things* — Don Norman
3. *Jakob Nielsen's 10 Usability Heuristics*
4. *About Face* — Alan Cooper
5. *Designing Interfaces* — Jenifer Tidwell
6. *Laws of UX* — Jon Yablonski
7. *Refactoring UI* — Adam Wathan & Steve Schoger
8. *100 Things Every Designer Needs to Know About People* — Susan Weinschenk
9. *Google Material Design Guidelines*
10. *Apple Human Interface Guidelines*

---

## UX-1: Self-Evidence & Learnability

*"Don't make me think." — Steve Krug*

The interface should be self-explanatory. Users should never have to stop and
wonder "What is this?", "Where am I?", or "What do I do next?"

### UX-1.1: Page Identity
- Every page has a clear, visible title that tells the user where they are
- The active navigation item is visually distinct from inactive items at all times
- Breadcrumbs or back-navigation make the user's location in the hierarchy obvious
- A new user can identify which page they're on within 1 second of looking

### UX-1.2: Clickability & Affordance
- Every clickable element looks clickable (buttons look like buttons, links look like links)
- Non-clickable elements do not look clickable (no misleading hover effects on static text)
- Interactive controls have visible boundaries or contrast that distinguish them from labels
- Icon-only buttons have tooltips or labels — no guessing what an icon means

### UX-1.3: First-Time Comprehension
- A user who has never seen Ralph Workflow can understand the purpose of each page from its layout and labels alone — without reading documentation
- Domain-specific terms (worktree, iteration, phase, checkpoint) are either self-explanatory in context or have inline explanations
- Empty states explain what the area is for and how to populate it, not just "No data"

### UX-1.4: Scanning, Not Reading
- Page layouts support scanning: headings, groupings, and whitespace guide the eye
- Important information is visually prominent (size, weight, position) — not buried in paragraphs
- Lists and tables use visual rhythm so users can scan rows without losing their place
- Call-to-action buttons are visually distinct from the surrounding content

---

## UX-2: Conceptual Model & Mental Maps

*"The design must convey the essence of how the system works." — Don Norman*

Users build mental models of how software works. The interface must reinforce
an accurate, simple model — not fight against it.

### UX-2.1: System Model Transparency
- The relationship between Workspace → Worktree → Session → Run is clear from the navigation structure
- The user can always answer: "What is running right now?" within 2 seconds of looking
- The pipeline concept (Plan → Develop → Review → Commit) is visually represented as a sequence, not hidden behind status labels
- The distinction between "global settings" and "project settings" is obvious without explanation

### UX-2.2: Natural Mapping
- Navigation order matches the conceptual workflow (overview → sessions → monitoring → config)
- The spatial layout of pipeline phases matches their temporal order (left-to-right or top-to-bottom)
- Related controls are grouped together; unrelated controls are separated by whitespace or dividers
- Status progression uses intuitive direction (progress moves forward, not backward)

### UX-2.3: Consistency of Metaphor
- The same concept is always represented the same way (a "session" always looks like a session everywhere it appears)
- Color meanings are consistent throughout the app (green always means success, red always means failure — never reversed)
- Iconography is consistent — the same icon always means the same thing, different things use different icons
- The workspace/IDE metaphor is consistent — the app never breaks into a different paradigm mid-flow

### UX-2.4: Visibility of System Status
- Running processes have visible, animated indicators (the user never wonders "is it still running?")
- Every long-running operation shows progress or at least an activity indicator
- Status changes are announced visually (not silently updated in the background)
- The user can distinguish between "nothing is happening" and "the system is working"

---

## UX-3: Nielsen's Heuristics Applied

*Jakob Nielsen's 10 Usability Heuristics — the gold standard checklist.*

### UX-3.1: Visibility of System Status
- The status bar always shows current system state (active runs, connection health)
- Log output clearly indicates whether it's live-streaming or showing cached/stale data
- When a session is launched, immediate visual feedback confirms the action succeeded
- Phase transitions in a running pipeline are visible in real-time, not only after polling

### UX-3.2: Match Between System and Real World
- Labels use developer-familiar language, not internal jargon ("Start Session" not "Instantiate Pipeline Executor")
- Time is displayed in human-relative terms where appropriate ("5 minutes ago" not just timestamps)
- Error messages describe the problem in terms the user understands and suggest what to do next
- Configuration labels match what a developer would call these concepts (not Ralph Workflow-internal naming)

### UX-3.3: User Control and Freedom
- Every wizard has a "Back" button — the user is never trapped in a forward-only flow
- Destructive actions (cancel run, delete worktree) require confirmation with clear consequences stated
- The user can always return to the home/dashboard from anywhere with one action
- Modals and dialogs have a clear escape route (X button, Escape key, click-outside)
- Configuration changes can be reverted before saving — the user is never forced to commit changes

### UX-3.4: Consistency and Standards
- Button placement follows a consistent pattern across all pages (primary action same position)
- All tables/lists use the same visual treatment (row height, padding, hover states)
- Form layouts are consistent — labels are always in the same position relative to inputs
- Keyboard shortcuts follow platform conventions (Ctrl+, for preferences, Ctrl+F for search)

### UX-3.5: Error Prevention
- Forms validate input before submission, not only after
- Dangerous configuration values show warnings before the user can save them
- The new session wizard prevents launch with invalid or missing configuration
- Closing a workspace with active runs warns the user before proceeding

### UX-3.6: Recognition Over Recall
- Recent sessions, recent workspaces, and recent templates are always visible — the user doesn't have to remember IDs or paths
- Configuration options show their current values, not blank fields the user must recall
- The session wizard shows a preflight summary so the user can verify choices without remembering what they picked 3 steps ago
- Status badges use both color AND text/icon — the user doesn't have to recall what each color means

### UX-3.7: Flexibility and Efficiency of Use
- Power users can navigate entirely via keyboard shortcuts
- Frequently used actions are accessible in 1-2 clicks from any page (new session, view active runs)
- The interface doesn't force experts through beginner workflows every time
- The user can reach any major section from any other section without going "home" first

### UX-3.8: Aesthetic and Minimalist Design
- Every visible element serves a purpose — no decorative-only elements that compete with content
- Information density is appropriate for a developer tool — not too sparse (wasted space) or too dense (overwhelming)
- Secondary information is available but doesn't compete with primary information for attention
- The visual hierarchy has clear levels — not everything screams for attention equally

### UX-3.9: Help Users Recover from Errors
- Failed runs show what went wrong, not just "Failed"
- Error states always include a recovery action (retry, resume, edit config, etc.)
- If a workspace can't be opened (path invalid), the error explains why and offers alternatives
- After fixing an error, the user returns to where they were — not dumped back to the beginning

### UX-3.10: Help and Documentation
- Keyboard shortcut help is discoverable and accessible from anywhere (? key)
- Configuration settings have contextual help (tooltips or descriptions) explaining what each one does
- The onboarding flow teaches by doing, not by showing walls of text
- Help content is positioned in context, next to the thing it explains — not in a separate help page

---

## UX-4: Goal-Directed Design

*"Design for user goals, not features." — Alan Cooper*

Users don't care about features — they care about accomplishing goals. The UI
should be organized around what users want to achieve, not around system architecture.

### UX-4.1: Primary User Goals
The interface must make these goals achievable with minimal friction:
- "I want to start a new AI development session" → achievable in ≤ 3 steps from any page
- "I want to see what's running right now" → visible immediately from the dashboard or status bar
- "I want to know if something went wrong" → failed/attention items are prominent, not hidden
- "I want to check the output of a run" → logs accessible in 1 click from any run reference
- "I want to change a setting" → find and modify any setting in ≤ 3 clicks
- "I want to resume a paused session" → resume action visible on the session itself, not in a menu

### UX-4.2: Task Flow Continuity
- Starting a session and monitoring it are a continuous flow — the user is taken to the run view after launching
- The user never has to re-enter information they already provided (wizard remembers state if you go back)
- Switching between workspaces preserves where the user was in each workspace
- Returning to a list after viewing a detail preserves scroll position and filters

### UX-4.3: Posture Appropriate Design
- The dashboard is a "glance and go" view — the user gets the status overview without interaction
- The run detail page is a "monitoring" view — designed for extended passive watching
- Configuration is a "focused task" view — organized for methodical review and editing
- The session list is a "management" view — designed for scanning, filtering, and bulk actions

### UX-4.4: Eliminate Excise
- The user is never forced to navigate to a different page to complete a simple action (e.g., resume should be inline, not on a separate page)
- No unnecessary confirmation dialogs for non-destructive actions
- The interface doesn't ask the user for information it could determine itself (auto-detect defaults)
- Repetitive tasks have shortcuts or batch operations — the user doesn't repeat the same 5 clicks N times

---

## UX-5: Progressive Disclosure & Information Architecture

*"Show the most important things first. Let people drill down for details." — Jenifer Tidwell*

### UX-5.1: Layered Complexity
- The dashboard shows summaries; detail is available on click — not everything dumped on one screen
- Configuration sections are collapsible — the user sees section headers first, expands what they need
- Advanced/rarely-used settings are hidden behind an "Advanced" section — not mixed with common settings
- Run detail starts with high-level status and phase; logs and iteration history are secondary panels

### UX-5.2: Information Hierarchy
- On every page, the most important information is in the top-left quadrant (F-pattern scanning)
- Status information is more prominent than metadata (what's happening NOW vs. when it was created)
- Actionable items (buttons, links) are more prominent than informational items
- Numbers and metrics that matter are large; supporting details are smaller

### UX-5.3: Grouping and Chunking
- Related settings are grouped into labeled sections (General, Execution, Retry, Git, Agents)
- Lists of more than 7-10 items are organized into categories or filterable — never a flat, unsorted dump
- The session list groups or filters by status so the user isn't scanning through completed sessions to find running ones
- Dashboard sections are clearly separated: active runs, attention needed, recent completions — not one merged list

### UX-5.4: Navigation Depth
- Any content in the app is reachable within 3 clicks from the dashboard
- The navigation structure is flat — not deeply nested (max 2 levels: section → detail)
- The sidebar provides direct access to all major sections without expanding menus
- The user never encounters a dead end — every page has clear paths forward or back

---

## UX-6: Cognitive Load & Psychology

*"People can only hold about 3-4 items in working memory." — based on Miller's Law (updated by Cowan)*

### UX-6.1: Hick's Law — Reduce Decision Time
- Menus and option lists have ≤ 7 items visible at once; longer lists have search or categorization
- The new session wizard makes decisions sequential (one choice per step) — not a single form with 15 fields
- Default values are provided for all configuration fields — the user only changes what they need
- Primary actions are visually distinguished from secondary actions — the user knows which button to press

### UX-6.2: Miller's Law — Chunk Information
- Statistics and metrics are grouped into digestible cards (3-4 per row max)
- Long configuration pages are broken into collapsible sections of 4-6 fields each
- The phase timeline has exactly 4 phases — presented as a unified visual chunk, not as 4 separate items
- Run metadata is organized into labeled groups, not a wall of key-value pairs

### UX-6.3: Fitts's Law — Make Targets Reachable
- Primary action buttons are large enough to click without precision (minimum 44px height)
- Frequently used actions are near the edges or corners where the cursor naturally rests
- The "New Session" button is always visible and large — not hidden in a menu
- Close/dismiss targets on modals are large enough to hit easily

### UX-6.4: Doherty Threshold — Keep It Fast
- Every user action produces visible feedback within 400ms
- If an operation takes more than 1 second, a loading indicator appears
- Perceived performance: skeleton screens or optimistic UI updates for data that's loading
- Workspace switching feels instant — no full page reload or blank screen

### UX-6.5: Jakob's Law — Leverage Familiarity
- The layout follows conventions users know from VS Code, JetBrains, or similar developer tools
- Sidebar + main content + status bar is the expected IDE pattern — don't invent a new paradigm
- Form controls behave like standard form controls (dropdowns drop down, toggles toggle)
- Keyboard shortcuts follow platform conventions that developers already know

### UX-6.6: Peak-End Rule — Endings Matter
- Run completion is celebrated — a clear success state with summary, not just "status: done"
- Failed runs end with helpful information (what went wrong, what to do) — not just a red badge
- The onboarding flow ends with a positive confirmation ("You're all set!") and a clear next action
- Closing the app preserves state — reopening feels like continuing, not starting over

### UX-6.7: Aesthetic-Usability Effect
- The visual design is polished enough that users perceive the tool as reliable and trustworthy
- Consistent spacing, alignment, and typography create a sense of quality
- Dark theme is well-executed — proper contrast, not just "dark backgrounds with unchanged text"
- Animations are subtle and purposeful — they reinforce actions, not distract from them

---

## UX-7: Visual Design Quality

*"Every design decision should be deliberate." — Refactoring UI*

### UX-7.1: Visual Hierarchy
- Size, weight, and color create a clear reading order on every page — the eye is guided, not lost
- No more than 3 levels of text hierarchy per page (heading, subheading, body)
- Important actions use the accent color; secondary actions use muted/outlined styles
- Data that changes (status, counts, progress) is more visually prominent than static labels

### UX-7.2: Spacing & Alignment
- All spacing follows a consistent scale (4px/8px grid) — no arbitrary gaps
- Elements that belong together are closer together; unrelated elements have more space between them (proximity principle)
- Text and controls are aligned to a grid — nothing looks "slightly off"
- Padding inside cards, panels, and dialogs is consistent throughout the app

### UX-7.3: Color with Purpose
- Color is never the sole indicator of meaning — always paired with text, icons, or shape
- The accent color (amber) is used sparingly for emphasis — not on every element
- Status colors (green/amber/red/blue) are reserved exclusively for status — not used decoratively
- Background surfaces use subtle value differences to create depth — not harsh color contrasts

### UX-7.4: Typography Discipline
- No more than 2 font families in the entire app (one UI font, one monospace for code/logs)
- Font sizes follow a defined scale — no arbitrary sizes sprinkled throughout
- Text contrast against dark backgrounds meets 4.5:1 minimum (WCAG AA)
- Monospace font is used only for code, paths, IDs, and log output — never for UI labels

### UX-7.5: Iconography
- All icons come from a single icon set with consistent stroke weight and style
- Icons always have a text label or tooltip — no unlabeled icons
- Icon size is consistent within the same context (all nav icons same size, all action icons same size)
- No emojis used as functional icons anywhere in the interface

---

## UX-8: Attention, Memory & Decision-Making

*"People don't want to think more than they have to." — Susan Weinschenk*

### UX-8.1: Attention Management
- Only one element per page demands primary attention (the hero stat, the active run, the save button)
- Animations are used to draw attention to changes — not running constantly as decoration
- Notification badges are used sparingly — only for genuinely actionable items
- The log viewer doesn't distract from the phase timeline and status when both are visible

### UX-8.2: Recognition-Aided Memory
- Worktrees and sessions are identified by descriptive names, not UUIDs
- Recently used items (templates, workspaces, sessions) are surfaced prominently
- The preflight summary in the wizard shows chosen values by name, not by internal key
- Status badges use consistent, recognizable visual patterns (shape + color + label)

### UX-8.3: Decision Support
- When the user must choose between options, the recommended/default option is indicated
- Configuration values show their defaults so the user knows what "normal" looks like
- The wizard doesn't present choices without context — each step explains what the choice affects
- Destructive actions look different from constructive actions (red vs. primary color)

### UX-8.4: Reducing Cognitive Overhead
- The user doesn't need to remember information from one page to use on another page
- Cross-references are links — if a session mentions a worktree, clicking the worktree name navigates there
- Status is shown in context — the worktree list shows run status inline, the user doesn't have to check sessions separately
- Numbers have units and context ("3 of 5 iterations" not just "3")

---

## UX-9: Feedback, States & Motion

*"Make state transitions feel natural and connected." — Material Design & Apple HIG*

### UX-9.1: State Communication
- Every interactive element has 4 visible states: default, hover, active/pressed, disabled
- Disabled elements are visually muted AND explain why they're disabled (tooltip or inline text)
- Empty states are designed — they show a message and suggest an action, not a blank void
- Loading states use skeletons or spinners — the screen never appears frozen or broken

### UX-9.2: Feedback Loop
- Clicking a button produces immediate visual feedback (color change, ripple, loading indicator)
- Form saves confirm success with a brief, non-blocking message (toast or inline confirmation)
- Errors appear next to the element that caused them, not in a generic alert at the top of the page
- Successful session launch transitions smoothly to the monitoring view — it's one continuous experience

### UX-9.3: Motion Principles
- Transitions between pages/views are smooth, not jarring instant swaps
- Motion conveys meaning: expanding = revealing more, collapsing = hiding, sliding = navigating
- No animation lasts more than 300ms for micro-interactions
- Animation can be reduced/disabled for users who prefer reduced motion

### UX-9.4: Real-Time Updates
- Live data (running sessions, log output) updates without the user refreshing or clicking
- The user can tell the difference between "data is updating live" and "data is stale"
- Status changes in the status bar and dashboard don't cause layout shifts or jarring reflows
- Auto-scrolling log output can be paused by the user scrolling up, and resumed with a "scroll to bottom" button

---

## UX-10: Platform Conventions & Familiarity

*"Consistency is one of the most powerful usability principles." — Apple HIG*

### UX-10.1: Desktop App Conventions
- The app has a native-feeling title bar with standard window controls (minimize, maximize, close)
- Standard keyboard shortcuts work as expected (Ctrl+C/V for copy/paste, Ctrl+Z for undo in text fields)
- Right-click context menus are available where users expect them (on list items, on text selections)
- The app follows OS-level dark/light mode preferences where applicable

### UX-10.2: Developer Tool Conventions
- The layout follows the sidebar + main content pattern familiar from VS Code, JetBrains, etc.
- Status bar at the bottom shows system state, like developer tools users already know
- Keyboard-first navigation is supported — mouse is not required for any primary workflow
- Configuration uses familiar patterns: sections, key-value forms, scope precedence display

### UX-10.3: Internal Consistency
- The same action always looks the same way (all "New" buttons use the same style, all "Delete" buttons use the same danger style)
- The same data is presented the same way everywhere it appears (timestamps always formatted the same, statuses always the same badges)
- Page layouts share a common structure (title → actions → content → secondary content)
- Terminology is consistent — the same concept is never called by two different names in different places

---

## UX-11: Error Handling & Edge Cases

*"Design for the unhappy path." — derived from About Face & Norman*

### UX-11.1: Graceful Degradation
- If the backend is unreachable, the UI shows a clear connection status — not a broken/blank screen
- If a workspace path no longer exists, the app explains and offers to remove it from the list
- If log streaming is interrupted, the viewer shows a reconnection indicator — not a silent stop
- Partial data is displayed when available — the app doesn't show nothing just because one field failed to load

### UX-11.2: Error Messages That Help
- Every error message states: what happened, why, and what the user can do about it
- Error messages use plain language — no stack traces, internal codes, or developer jargon shown to the user
- Errors are shown in context (next to the field, next to the failing run) — not in a generic alert
- Error states include an action: retry, edit, dismiss, go back — never a dead end

### UX-11.3: Prevention Over Recovery
- Forms validate on blur (when leaving a field), not after submit — catch problems early
- Dangerous settings display a warning before the user saves — not after damage is done
- The wizard blocks launch if critical configuration is missing (e.g., no API key) with a clear explanation
- Navigating away from unsaved changes triggers a save/discard/cancel dialog

---

## UX-12: Accessibility as UX

*"Accessibility is not a feature. It is a quality requirement." — all sources*

### UX-12.1: Perceivable
- All text meets WCAG AA contrast (4.5:1 for body text, 3:1 for large text) against dark backgrounds
- Information is not conveyed by color alone — always paired with text, icon, or pattern
- Focus indicators are clearly visible (not hidden by the dark theme)
- Status changes are announced to screen readers via live regions

### UX-12.2: Operable
- Every feature is accessible via keyboard — no mouse-only interactions
- Focus order follows visual reading order (top-to-bottom, left-to-right)
- No keyboard traps — the user can always Tab/Escape out of any component
- Custom keyboard shortcuts don't override system or screen reader shortcuts

### UX-12.3: Understandable
- Labels are descriptive — "Save Global Configuration" not just "Save"
- Error messages identify the field and the problem — "Iterations must be between 1 and 20" not "Invalid value"
- Consistent navigation means the user can predict where things are after learning the interface once
- Interactive elements behave predictably — no surprise side effects

---

## UX-13: Emotional Design & Trust

*"People judge a product's credibility by its visual design." — Weinschenk, Norman*

### UX-13.1: Confidence & Control
- The user always feels in control — the system doesn't do things without the user's knowledge
- When AI is running in the background, the UI makes this visible — it's not a black box
- The user can always stop/cancel what's happening — they are never a passive spectator
- Confirmation of destructive actions includes what will be lost, not just "Are you sure?"

### UX-13.2: Professional Polish
- No placeholder content, broken layouts, or obviously incomplete sections in shipped UI
- Loading states, empty states, and error states are all designed — not generic framework defaults
- Transitions and animations are smooth — no jank, flicker, or layout thrashing
- The app feels cohesive — every screen looks like it belongs to the same product

### UX-13.3: Transparency
- The user can always understand what Ralph Workflow is doing and why (current phase, current agent, iteration count)
- Configuration shows where values come from (default vs. global vs. project) — no mystery overrides
- When degradation occurs (fallback agents, retries), the user is informed — not kept in the dark
- The effective configuration view shows the final merged result so the user knows exactly what will run

---

## Ongoing Practice: How to Keep UX Alive

### UX Review as a Habit

UX is not a gate you pass through once. Every change to the UI — adding a
button, rearranging a layout, introducing a new screen — is an opportunity
to improve or regress. The principles above should be part of the design
conversation, not a post-hoc audit.

**Before every UI change, ask:**
1. Does this make the user think less or more? (UX-1, Krug)
2. Does this match their mental model? (UX-2, Norman)
3. Does this serve a user goal or just expose a feature? (UX-4, Cooper)
4. Am I showing the right amount of information? (UX-5, Tidwell)
5. Am I adding to cognitive load? (UX-6, Yablonski)

**After every UI change, ask:**
1. Can someone who didn't design this figure it out instantly?
2. Did I introduce any inconsistency with the rest of the app?
3. What happens when this goes wrong — is the error path designed too?
4. Would I notice if this was live-updating vs. stale?

### The Krug Test (Do This Regularly)

Steve Krug's core method: watch someone use the app for 5 minutes. Don't
explain anything. Don't help. Just watch. If they hesitate, squint, or ask
"what does this do?" — that's a UX failure, no matter how logical the design
seemed in your head.

This doesn't require formal usability testing. It can be:
- A colleague who hasn't used the app recently
- Yourself, after not looking at a screen for a week
- A fresh set of eyes on a screen you've been working on

The question is always the same: **did they have to think?**

### The Five-Second Test

Show someone a screen for 5 seconds. Then take it away and ask:
- What is this page for?
- What's the most important thing on it?
- What would you do first?

If they can't answer these, the visual hierarchy and self-evidence need work.
This test should be applied to every page in the app, and re-applied when
that page changes significantly.

### Evolving Priorities

The priority of UX principles shifts as the product matures:

**Early stage (now):** Focus on UX-1 (self-evidence), UX-2 (mental model),
UX-4 (goal-directed design), and UX-3.3 (user control). Get the fundamentals
right. A user who can't figure out the basic flow won't stick around to
appreciate your animations.

**Growth stage:** Layer in UX-5 (progressive disclosure), UX-6 (cognitive
load), UX-9 (feedback and states). The basics work — now make them feel
effortless. This is where the difference between "usable" and "pleasant"
lives.

**Mature stage:** Polish with UX-7 (visual quality), UX-8 (attention
management), UX-13 (emotional design). The app works well — now make it
feel world-class. This is what separates tools people tolerate from tools
people recommend.

**Always, at every stage:** UX-11 (error handling), UX-12 (accessibility),
UX-3 (Nielsen's heuristics). These are non-negotiable regardless of maturity.
Broken error states and inaccessible interfaces are never acceptable.

### Regression Awareness

UX quality regresses. New features get bolted on. Edge cases accumulate.
Screens that were clean at launch get cluttered. This is natural — but it
must be actively managed.

Signs of UX regression to watch for:
- A screen that used to be simple now has "just one more thing" added
- Users are asking questions about things that used to be obvious
- New features require explanation that existing features didn't
- Keyboard shortcuts or conventions are inconsistent between old and new screens
- The "happy path" still works but error/empty/loading states for new features are undesigned

When regression is detected, the response is not "we'll fix it later."
The response is to fix it as part of the work that caused the regression.

---

## Core User Tasks

These tasks represent what users actually want to accomplish. Every design
decision should be tested against these — not just once, but every time the
relevant screens change.

| # | Task | Relevant Principles |
|---|------|---------------------|
| T1 | First-time user opens app and starts a session | UX-1.3, UX-4.1, UX-6.1, UX-10.1 |
| T2 | Check status of all running sessions | UX-2.4, UX-3.1, UX-4.1, UX-5.2 |
| T3 | Investigate a failed run and resume it | UX-3.9, UX-4.1, UX-11.2, UX-13.1 |
| T4 | Change a configuration setting | UX-3.6, UX-4.1, UX-5.1, UX-6.1 |
| T5 | Monitor a run's progress in real-time | UX-2.4, UX-6.4, UX-9.4, UX-13.3 |
| T6 | Switch between workspaces | UX-4.2, UX-6.4, UX-6.5, UX-10.2 |
| T7 | Find and resume a session from yesterday | UX-3.6, UX-5.3, UX-8.2, UX-8.4 |
| T8 | Understand why a run is in degraded state | UX-2.1, UX-3.2, UX-11.2, UX-13.3 |

These tasks should grow as the product grows. When a new feature is added,
ask: "What user task does this serve?" and add it to this list. If you can't
answer that question, reconsider whether the feature belongs.

---

## Current Priority Focus

These priorities reflect where Ralph Workflow is **right now**. They should be
revisited as the product evolves.

| Focus | Principles | Why Now |
|-------|-----------|--------|
| **Foundation** | UX-1 (Self-Evidence), UX-2 (Mental Model), UX-3.3 (User Control), UX-3.9 (Error Recovery) | Without these, users can't effectively use the tool at all. Everything else is premature optimization. |
| **Effectiveness** | UX-4 (Goal-Directed), UX-5 (Progressive Disclosure), UX-6 (Cognitive Load), UX-9 (Feedback & States) | These determine whether using the tool daily is pleasant or painful. |
| **Quality** | UX-7 (Visual Design), UX-8 (Attention & Memory), UX-10 (Platform Conventions) | These elevate the tool from functional to polished — from "it works" to "it feels good." |
| **Excellence** | UX-13 (Emotional Design), UX-12 (Deep Accessibility) | These make the tool feel world-class — something users recommend, not just use. |
| **Always** | UX-3 (Heuristics), UX-11 (Error Handling), UX-12.1 (Basic Accessibility) | Non-negotiable at every stage. Never deprioritize these. |
