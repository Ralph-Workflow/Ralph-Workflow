# Functional Transformations

This document is a practical cookbook for writing Rust that satisfies the repository's
functional-programming lints (`forbid_mut_binding`, `forbid_imperative_loops`,
`forbid_mutating_receiver_methods`, `forbid_interior_mutability`).

Every example here is written for non-boundary domain code.  Boundary modules (`io/`,
`runtime/`, `ffi/`, `boundary/`) are exempt from these lints and may use mutation when
the underlying API demands it.

The examples use types and patterns drawn from this project's actual codebase so you
can see how the techniques look at production scale, not just on textbook snippets.

## Building collections

### Vec from a filter-map pipeline

Assembling a markdown document from optional sections — filter empty strings out, then
join the survivors:

```rust
pub fn format_plan_as_markdown(elements: &PlanElements) -> String {
    let summary = format_summary_section(elements);
    let skills = format_skills_section(elements);
    let steps = format_steps_section(elements);
    let critical = format_critical_files_section(elements);
    let risks = format_risks_section(elements);
    let verification = format_verification_section(elements);

    [summary, skills, steps, critical, risks, verification]
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}
```

Each section renderer returns `String`.  Empty means "nothing to show."  No mutable
accumulator needed — `filter` + `join` does the assembly.

### HashMap by collecting pairs

When you need a lookup table, map each item to a `(key, value)` tuple and collect:

```rust
/// Build a prompt-history index from a list of prompt records.
fn build_prompt_index(records: &[PromptRecord]) -> HashMap<String, PromptHistoryEntry> {
    records
        .iter()
        .map(|record| {
            let entry = PromptHistoryEntry {
                content: record.content.clone(),
                timestamp: record.timestamp,
            };
            (record.id.clone(), entry)
        })
        .collect()
}
```

Compare with the imperative version this replaces:

```rust
// bad — mutable map + for loop
let mut index = HashMap::new();
for record in records {
    let entry = PromptHistoryEntry {
        content: record.content.clone(),
        timestamp: record.timestamp,
    };
    index.insert(record.id.clone(), entry);
}
```

### BTreeMap for deterministic ordering

When you need sorted keys — for deterministic output in tests, serialisation, or
user-facing reports — collect into `BTreeMap` instead of sorting a `HashMap`:

```rust
/// Group issues by file path for display, with deterministic file ordering.
fn group_issues_by_file(issues: &[ParsedIssue]) -> BTreeMap<String, Vec<ParsedIssue>> {
    issues.iter().fold(BTreeMap::new(), |groups, issue| {
        let key = issue
            .file
            .clone()
            .unwrap_or_else(|| "(no file)".to_string());
        // Build a new map with the issue appended to the right group.
        let existing: Vec<ParsedIssue> = groups
            .get(&key)
            .cloned()
            .unwrap_or_default();
        let updated = existing
            .into_iter()
            .chain(std::iter::once(issue.clone()))
            .collect();
        groups
            .into_iter()
            .chain(std::iter::once((key, updated)))
            .collect()
    })
}
```

When grouping is performance-sensitive or when this is boundary code, the mutable
`BTreeMap::entry` API is appropriate — place it in a boundary module:

```rust
// boundary module — mutation is fine here
let mut grouped: BTreeMap<String, Vec<ParsedIssue>> = BTreeMap::new();
for issue in parsed {
    let key = issue.file.clone().unwrap_or_else(|| "(no file)".to_string());
    grouped.entry(key).or_default().push(issue);
}
```

### HashSet from an iterator

Deduplicate agent names by collecting into a set:

```rust
fn unique_agent_names(chain: &AgentChainState) -> HashSet<String> {
    chain.agents.iter().cloned().collect()
}
```

## String building

### Joining lines with a separator

```rust
fn format_scope_items(items: &[ScopeItem]) -> String {
    items
        .iter()
        .map(|item| {
            let count_suffix = item
                .count
                .as_ref()
                .map(|c| format!(" **{c}** "))
                .unwrap_or_default();
            let category_suffix = item
                .category
                .as_ref()
                .map(|c| format!(" ({c})"))
                .unwrap_or_default();
            format!("- {count_suffix}{}{category_suffix}", item.description)
        })
        .collect::<Vec<_>>()
        .join("\n")
}
```

The pattern is: `map` each element to its formatted string, `collect` into a `Vec`,
then `join`.  This replaces the imperative approach of pushing onto a `String` with
`if i > 0 { output.push_str(", "); }` guard logic.

### Collecting into a single String (no separator)

When you need concatenation rather than joining, `collect::<String>()` works directly:

```rust
fn render_inline_content(content: &[InlineElement]) -> String {
    content
        .iter()
        .map(|e| match e {
            InlineElement::Text(s) => s.clone(),
            InlineElement::Emphasis(s) => format!("**{s}**"),
            InlineElement::Code(s) => format!("`{s}`"),
            InlineElement::Link { href, text } => format!("[{text}]({href})"),
        })
        .collect()
}
```

`String` implements `FromIterator<String>`, so `collect()` concatenates all fragments.

### Chaining sections from multiple sources

The `chain` combinator merges iterators from different sources into one pipeline:

```rust
fn format_skills_section(skills: &[Skill], mcps: &[McpEntry], raw: Option<&str>) -> String {
    let parts: Vec<String> = skills
        .iter()
        .map(|skill| {
            skill
                .reason
                .as_ref()
                .map(|r| format!("- **Skill:** {} \u{2014} {}", skill.name, r))
                .unwrap_or_else(|| format!("- **Skill:** {}", skill.name))
        })
        .chain(mcps.iter().map(|mcp| {
            mcp.reason
                .as_ref()
                .map(|r| format!("- **MCP:** {} \u{2014} {}", mcp.name, r))
                .unwrap_or_else(|| format!("- **MCP:** {}", mcp.name))
        }))
        .chain(raw.iter().map(|c| c.trim().to_string()))
        .filter(|s| !s.is_empty())
        .collect();

    if parts.is_empty() {
        return String::new();
    }

    format!("### Skills & MCP Recommendations\n\n{}\n", parts.join("\n"))
}
```

`chain` is the functional equivalent of "append the second list to the first."  No
mutable accumulator, no `extend`, no `push_str`.

### Multi-stage line processing

Parsing a human-written file list into typed change records, using successive `map`
stages as a pipeline:

```rust
fn parse_files_changed_list(files: &str) -> Vec<(String, ChangeAction)> {
    files
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .map(|l| l.trim_start_matches("- ").trim())
        .map(|l| {
            let lowered = l.to_ascii_lowercase();
            let action = if lowered.contains("(created)") || lowered.contains("(new)") {
                ChangeAction::Create
            } else if lowered.contains("(deleted)") || lowered.contains("(removed)") {
                ChangeAction::Delete
            } else {
                ChangeAction::Modify
            };
            let path = l.split_once(" (").map_or(l, |(p, _)| p).trim().to_string();
            (path, action)
        })
        .collect()
}
```

Each `map` stage transforms the data one step further.  The pipeline reads top to
bottom like a recipe, and every intermediate value is immutable.

## Replacing common imperative loops

### filter_map — transform and keep only the successes

```rust
/// Extract agent names from config entries that have an agent assigned.
fn active_agents(entries: &[PhaseConfig]) -> Vec<String> {
    entries
        .iter()
        .filter_map(|entry| entry.agent.as_ref())
        .cloned()
        .collect()
}
```

Compare:

```rust
// bad — for loop with conditional push
let mut agents = Vec::new();
for entry in entries {
    if let Some(agent) = &entry.agent {
        agents.push(agent.clone());
    }
}
```

### flat_map — flatten nested structures

Collect all issues across all review passes into a single list:

```rust
fn all_issues(passes: &[ReviewPass]) -> Vec<ReviewIssue> {
    passes
        .iter()
        .flat_map(|pass| pass.issues.iter().cloned())
        .collect()
}
```

### Collect into Result — fail on the first error

When each element can fail validation, `collect` into `Result<Vec<_>, _>`.  This
short-circuits on the first error, just like a `for` loop with `?`:

```rust
fn validate_all_steps(steps: &[RawStep]) -> Result<Vec<ValidatedStep>, StepValidationError> {
    steps.iter().map(validate_step).collect()
}
```

Under the hood, `Result` implements `FromIterator`: it collects successes into a `Vec`
or returns the first `Err`.

When you only need to validate without collecting results:

```rust
fn check_all_files_exist(
    workspace: &dyn Workspace,
    paths: &[String],
) -> Result<(), FileNotFoundError> {
    paths.iter().try_for_each(|path| {
        workspace
            .exists(path)
            .then_some(())
            .ok_or_else(|| FileNotFoundError(path.clone()))
    })
}
```

### fold — state evolution without mutable accumulators

The reducer pattern is the project's standard fold:

```rust
pub fn reduce(state: PipelineState, event: PipelineEvent) -> PipelineState {
    match event {
        PipelineEvent::Lifecycle(e) => reduce_lifecycle_event(state, e),
        PipelineEvent::Planning(e) => reduce_planning_event(state, e),
        PipelineEvent::Development(e) => reduce_development_event(state, e),
        PipelineEvent::Review(e) => reduce_review_event(state, e),
        PipelineEvent::ContextCleaned => PipelineState {
            context_cleaned: true,
            ..state
        },
        PipelineEvent::CheckpointSaved { .. } => PipelineState {
            checkpoint_saved_count: state.checkpoint_saved_count.saturating_add(1),
            ..state
        },
        // ...remaining variants
    }
}
```

When replaying a sequence of events:

```rust
let final_state = events
    .into_iter()
    .fold(PipelineState::default(), reduce);
```

No `let mut state`.  The fold's accumulator is moved into each call and a new value
comes out.

### partition — split a collection by predicate

```rust
fn split_by_severity(issues: Vec<ReviewIssue>) -> (Vec<ReviewIssue>, Vec<ReviewIssue>) {
    issues.into_iter().partition(|issue| issue.is_blocking())
}
```

Compare:

```rust
// bad — two mutable vecs + for loop
let mut blocking = Vec::new();
let mut non_blocking = Vec::new();
for issue in issues {
    if issue.is_blocking() {
        blocking.push(issue);
    } else {
        non_blocking.push(issue);
    }
}
```

### unzip — separate pairs into parallel collections

```rust
fn split_agent_models(
    entries: Vec<AgentModelEntry>,
) -> (Vec<String>, Vec<Vec<String>>) {
    entries
        .into_iter()
        .map(|entry| (entry.agent_name, entry.models))
        .unzip()
}
```

### find and find_map — locate a specific element

```rust
fn first_blocking_issue(issues: &[ReviewIssue]) -> Option<&ReviewIssue> {
    issues.iter().find(|issue| issue.is_blocking())
}

fn first_error_message(events: &[PipelineEvent]) -> Option<String> {
    events.iter().find_map(|event| match event {
        PipelineEvent::Agent(AgentEvent::InvocationFailed { error, .. }) => {
            Some(error.to_string())
        }
        _ => None,
    })
}
```

### any and all — boolean checks over collections

```rust
fn has_blocking_issues(state: &ReviewState) -> bool {
    state.issues.iter().any(|issue| issue.is_blocking())
}

fn all_steps_completed(steps: &[ExecutionStep]) -> bool {
    steps.iter().all(|step| step.status == StepStatus::Completed)
}
```

### count and sum — aggregate without mutable accumulators

```rust
fn completed_iteration_count(metrics: &RunMetrics) -> u32 {
    // Already a field, but if computed from events:
    // events.iter().filter(|e| matches!(e, PipelineEvent::Development(
    //     DevelopmentEvent::IterationCompleted { output_valid: true, .. }
    // ))).count() as u32
    metrics.dev_iterations_completed
}

fn total_issue_count(passes: &[ReviewPass]) -> usize {
    passes.iter().map(|pass| pass.issues.len()).sum()
}
```

### enumerate — index-aware iteration

```rust
fn format_numbered_steps(steps: &[Step]) -> String {
    steps
        .iter()
        .enumerate()
        .map(|(i, step)| format!("{}. [{}] {}", i.saturating_add(1), step.kind, step.description))
        .collect::<Vec<_>>()
        .join("\n")
}
```

## Sorting, deduplication, and reordering

These `Vec` methods take `&mut self` and are caught by `forbid_mutating_receiver_methods`.

### Sorting — use BTreeMap or itertools

When the sort key is the natural key, collect into a `BTreeMap`:

```rust
/// Build a deterministically-ordered index of agents to their model lists.
fn ordered_agent_index(
    agents: &[String],
    models: &[Vec<String>],
) -> BTreeMap<String, Vec<String>> {
    agents
        .iter()
        .zip(models.iter())
        .map(|(agent, model_list)| (agent.clone(), model_list.clone()))
        .collect()
}
```

When you need a sorted `Vec`, use `itertools::sorted_by_key`:

```rust
use itertools::Itertools;

fn steps_by_priority(steps: Vec<Step>) -> Vec<Step> {
    steps.into_iter().sorted_by_key(|s| s.priority).collect()
}
```

### Deduplication — collect into a set, or itertools

```rust
use itertools::Itertools;

fn unique_file_paths(issues: &[ReviewIssue]) -> Vec<String> {
    issues
        .iter()
        .filter_map(|issue| issue.file.clone())
        .unique()
        .collect()
}
```

`unique()` preserves first-occurrence order.  If order does not matter, collect into
a `BTreeSet` or `HashSet`.

### Reversing

```rust
fn most_recent_first(history: Vec<ExecutionStep>) -> Vec<ExecutionStep> {
    history.into_iter().rev().collect()
}
```

### Taking a prefix

```rust
fn latest_events(events: Vec<PipelineEvent>, limit: usize) -> Vec<PipelineEvent> {
    events.into_iter().rev().take(limit).collect()
}
```

## Struct updates without mutation

### Struct-update syntax in reducers

The `..state` spread is the workhorse of the reducer — change one or two fields, carry
everything else forward:

```rust
PipelineEvent::ContextCleaned => PipelineState {
    context_cleaned: true,
    ..state
},
PipelineEvent::FinalizingStarted => PipelineState {
    phase: PipelinePhase::Finalizing,
    ..state
},
```

This also works with nested state:

```rust
PipelineEvent::LoopRecoveryTriggered { .. } => {
    let artifact = state
        .continuation
        .current_artifact
        .unwrap_or(ArtifactType::Plan);

    PipelineState {
        continuation: state.continuation.reset().with_artifact(artifact),
        agent_chain: state.agent_chain.clear_session_id(),
        ..state
    }
},
```

### The `with_*` method pattern

This is the project's standard approach for chainable value construction.  Each method
takes `mut self` and returns `Self`.  The `mut` is on the consumed value — it is not
visible to the caller:

```rust
impl Config {
    #[must_use]
    pub const fn with_developer_iters(mut self, iters: u32) -> Self {
        self.developer_iters = iters;
        self
    }

    #[must_use]
    pub const fn with_verbosity(mut self, verbosity: Verbosity) -> Self {
        self.verbosity = verbosity;
        self
    }

    #[must_use]
    pub fn with_developer_agent(mut self, agent: String) -> Self {
        self.developer_agent = Some(agent);
        self
    }
}
```

Usage at the call site — no mutable binding:

```rust
let config = Config::for_test()
    .with_developer_iters(3)
    .with_verbosity(Verbosity::Quiet)
    .with_developer_agent("claude".to_string());
```

**Why `mut self` is allowed here.**  The lint `forbid_mut_binding` fires on `let mut x`
and `fn foo(mut x: T)` at the *call site*.  A `with_*` method's `mut self` parameter is
the *callee's* internal concern — the caller never writes `mut`.  The caller sees a
value-in, value-out transformation, which is the FP contract.

### The `with_*` pattern for multi-field resets

When a state transition needs to reset several fields at once, combine `with_*` with
struct-update syntax.  Preserve configured limits, reset everything else:

```rust
impl ContinuationState {
    #[must_use]
    pub fn reset(self) -> Self {
        Self {
            max_xsd_retry_count: self.max_xsd_retry_count,
            max_same_agent_retry_count: self.max_same_agent_retry_count,
            max_continue_count: self.max_continue_count,
            max_fix_continue_count: self.max_fix_continue_count,
            max_consecutive_same_effect: self.max_consecutive_same_effect,
            ..Self::default()
        }
    }
}
```

At the call site, chain it with other state updates:

```rust
PipelineState {
    continuation: state.continuation.reset().with_artifact(artifact),
    agent_chain: state.agent_chain.clear_session_id(),
    ..state
}
```

### State machine transitions that build entirely new values

For complex structs with many fields, the `advance_to_next_model` pattern constructs
a complete new `Self` rather than mutating fields.  This makes every possible next-state
explicit:

```rust
impl AgentChainState {
    #[must_use]
    pub fn switch_to_next_agent(&self) -> Self {
        if self.current_agent_index + 1 < self.agents.len() {
            Self {
                agents: Arc::clone(&self.agents),
                current_agent_index: self.current_agent_index + 1,
                models_per_agent: Arc::clone(&self.models_per_agent),
                current_model_index: 0,
                retry_cycle: self.retry_cycle,
                max_cycles: self.max_cycles,
                backoff_pending_ms: None,
                current_role: self.current_role,
                current_drain: self.current_drain,
                current_mode: self.current_mode,
                rate_limit_continuation_prompt: self.rate_limit_continuation_prompt.clone(),
                last_session_id: self.last_session_id.clone(),
                // ...remaining fields
            }
        } else {
            // Wrap around: reset index, increment retry cycle
            let new_retry_cycle = self.retry_cycle + 1;
            Self {
                agents: Arc::clone(&self.agents),
                current_agent_index: 0,
                current_model_index: 0,
                retry_cycle: new_retry_cycle,
                // ...remaining fields
            }
        }
    }
}
```

Use `Arc::clone` for shared collections that appear in the new value unchanged.  This
is cheap (reference count bump) and avoids deep-copying large agent/model lists.

### Consuming builders

When a type needs incremental construction, use consuming builders — each method takes
`self` and returns `Self`:

```rust
struct ReviewRequestBuilder {
    prompt: Option<String>,
    pass: Option<u32>,
    depth: ReviewDepth,
}

impl ReviewRequestBuilder {
    fn new() -> Self {
        Self {
            prompt: None,
            pass: None,
            depth: ReviewDepth::Standard,
        }
    }

    #[must_use]
    fn prompt(self, prompt: impl Into<String>) -> Self {
        Self {
            prompt: Some(prompt.into()),
            ..self
        }
    }

    #[must_use]
    fn pass(self, pass: u32) -> Self {
        Self {
            pass: Some(pass),
            ..self
        }
    }

    #[must_use]
    fn depth(self, depth: ReviewDepth) -> Self {
        Self { depth, ..self }
    }

    fn build(self) -> Result<ReviewRequest, BuildError> {
        let prompt = self.prompt.ok_or(BuildError::MissingPrompt)?;
        Ok(ReviewRequest {
            prompt,
            pass: self.pass.unwrap_or(1),
            depth: self.depth,
        })
    }
}

// usage — no &mut self, no let mut
let request = ReviewRequestBuilder::new()
    .prompt("review the implementation")
    .pass(2)
    .depth(ReviewDepth::Thorough)
    .build()?;
```

When all fields are known upfront, skip the builder and use a struct literal.

## Conditional transformation chains

### Shadowing — the FP-idiomatic rebind

Shadowing is not mutation.  Each `let` creates a new binding:

```rust
let description = raw_description;
let description = description.trim();
let description = if description.is_empty() {
    "(no description)"
} else {
    description
};
```

This reads top-to-bottom as a sequence of refinements.

### Chaining Option with or / map / and_then

```rust
fn resolve_agent_name(config: &Config, role: AgentRole) -> String {
    match role {
        AgentRole::Developer => config
            .developer_agent
            .clone()
            .unwrap_or_else(|| "default-dev".to_string()),
        AgentRole::Reviewer => config
            .reviewer_agent
            .clone()
            .unwrap_or_else(|| "default-reviewer".to_string()),
    }
}
```

For multi-source fallback:

```rust
fn resolve_display_name(user: &UserRecord) -> String {
    user.nickname
        .as_deref()
        .or(user.full_name.as_deref())
        .unwrap_or("anonymous")
        .to_string()
}
```

### Chaining Result with and_then

```rust
fn load_and_parse_plan(
    workspace: &dyn Workspace,
    path: &str,
) -> Result<PlanDocument, LoadPlanError> {
    workspace
        .read(path)
        .map_err(|_| LoadPlanError::MissingFile(path.to_string()))
        .and_then(|contents| {
            parse_plan(&contents).map_err(LoadPlanError::InvalidFormat)
        })
}
```

### Conditional transformations on a value

When transformations are conditional, use `if`/`else` expressions with shadowing
rather than `let mut`:

```rust
let prompt = base_prompt;
let prompt = if config.features.include_context {
    format!("{prompt}\n\n{}", context_section)
} else {
    prompt
};
let prompt = if config.features.include_history {
    format!("{prompt}\n\n{}", history_section)
} else {
    prompt
};
```

For many optional transforms, fold over a list:

```rust
let transforms: Vec<fn(String) -> String> = [
    config.features.include_context.then_some(
        (|p| format!("{p}\n\n{}", context_section)) as fn(String) -> String,
    ),
    config.features.include_history.then_some(
        (|p| format!("{p}\n\n{}", history_section)) as fn(String) -> String,
    ),
]
.into_iter()
.flatten()
.collect();

let prompt = transforms
    .into_iter()
    .fold(base_prompt, |p, f| f(p));
```

## The fold-with-mut-accumulator trap

This pattern looks functional but violates `forbid_mut_binding`:

```rust
// bad — mut acc inside fold closure
let map = items.iter().fold(HashMap::new(), |mut acc, item| {
    acc.insert(item.key.clone(), item.value.clone());
    acc
});
```

The `mut acc` binding triggers the lint.  The fix is to avoid the mutable accumulator:

```rust
// good — map to pairs, then collect
let map: HashMap<_, _> = items
    .iter()
    .map(|item| (item.key.clone(), item.value.clone()))
    .collect();
```

Use `fold` when the accumulator type is a domain struct with a `with_*` method — that
way the fold body is a value-to-value transformation with no `mut`:

```rust
let state = events
    .into_iter()
    .fold(PipelineState::default(), reduce);
```

## Trait implementations

### Display for domain types

`Display::fmt` receives `f: &mut Formatter`.  The `write!` macro calls `&mut self`
methods on the formatter.  This is intrinsic to Rust's formatting machinery, not domain
mutation.

Place `Display` impls on domain types when the format is part of the type's identity
(e.g., error messages).  Place them in rendering or boundary modules when the format is
presentation-specific.

```rust
impl fmt::Display for ErrorEvent {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::AgentChainExhausted { role, phase, cycle } => {
                write!(f, "agent chain exhausted for {role:?} in {phase:?} after {cycle} cycles")
            }
            Self::WorkspaceReadFailed { path, kind } => {
                write!(f, "workspace read failed: {path} ({kind:?})")
            }
            // ...remaining variants
        }
    }
}
```

### FromIterator for domain collection types

Implementing `FromIterator` lets a domain type participate in `collect()` pipelines:

```rust
struct IssueList {
    items: Vec<ReviewIssue>,
}

impl FromIterator<ReviewIssue> for IssueList {
    fn from_iter<I: IntoIterator<Item = ReviewIssue>>(iter: I) -> Self {
        Self {
            items: iter.into_iter().collect(),
        }
    }
}

// usage — no mutation visible at the call site
let issues: IssueList = raw_issues
    .into_iter()
    .filter(|issue| issue.is_actionable())
    .collect();
```

## Quick reference table

| Imperative pattern | Functional replacement |
|---|---|
| `let mut v = Vec::new(); for x in xs { v.push(f(x)); }` | `xs.into_iter().map(f).collect()` |
| `let mut v = Vec::new(); for x in xs { if p(x) { v.push(x); } }` | `xs.into_iter().filter(p).collect()` |
| `let mut v = Vec::new(); for x in xs { if let Some(y) = g(x) { v.push(y); } }` | `xs.into_iter().filter_map(g).collect()` |
| `let mut v = Vec::new(); for x in xs { v.extend(x.children()); }` | `xs.into_iter().flat_map(\|x\| x.children()).collect()` |
| `let mut n = 0; for x in xs { n += x.score; }` | `xs.iter().map(\|x\| x.score).sum()` |
| `let mut n = 0; for x in xs { if p(x) { n += 1; } }` | `xs.iter().filter(\|x\| p(x)).count()` |
| `let mut s = init; for x in xs { s = f(s, x); }` | `xs.into_iter().fold(init, f)` |
| `for x in xs { check(x)?; }` | `xs.iter().try_for_each(check)?` |
| `for x in xs { if p(x) { return Some(x); } } None` | `xs.iter().find(\|x\| p(x))` |
| `for x in xs { if let Some(y) = g(x) { return Some(y); } } None` | `xs.iter().find_map(g)` |
| `let mut b = false; for x in xs { if p(x) { b = true; break; } }` | `xs.iter().any(p)` |
| `let mut b = true; for x in xs { if !p(x) { b = false; break; } }` | `xs.iter().all(p)` |
| `let (mut a, mut b) = …; for x { if p(x) { a.push(x) } else { b.push(x) } }` | `xs.into_iter().partition(p)` |
| `let (mut ks, mut vs) = …; for (k, v) { ks.push(k); vs.push(v); }` | `pairs.into_iter().unzip()` |
| `let mut xs = get(); xs.sort_by_key(f);` | `get().into_iter().sorted_by_key(f).collect()` (itertools) |
| `let mut xs = get(); xs.dedup();` | `get().into_iter().unique().collect()` (itertools) |
| `let mut xs = get(); xs.reverse();` | `get().into_iter().rev().collect()` |
| `let mut xs = get(); xs.truncate(n);` | `get().into_iter().take(n).collect()` |
| `map.insert(k, v);` | rebuild with `.chain([(k, v)]).collect()` |
| `vec.push(item);` | rebuild with `.chain([item]).collect()` |

## When to use boundary modules instead

Some patterns are inherently imperative and belong in boundary code:

- I/O retry loops
- Byte-by-byte parsing with `Read`
- Process polling with `wait()`
- Building output strings with `write!` and `writeln!` (rendering modules)
- Grouping into `BTreeMap` via `.entry().or_default().push()` (when performance matters)
- Writing to `BufWriter` or network sockets
- Filling a buffer from `stdin`

Place these in `io/`, `runtime/`, `ffi/`, or `boundary/` modules where the lints are
exempt.  See `docs/code-style/boundaries.md` for guidance on module placement.
