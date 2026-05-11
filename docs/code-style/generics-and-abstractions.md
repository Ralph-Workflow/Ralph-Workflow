# Generics And Abstractions

> **Historical Rust-era documentation** — This file describes the retired Rust implementation's generics and abstractions guidance. The current Python package under `ralph-workflow/` follows different patterns. Treat this file as archival background only.

This document defines when generic techniques are worth using in the project and when explicit code is better.

## Core rule

Start with explicit Rust.

Reach for generics and abstraction only when they remove real duplication, preserve type safety, and keep the code easier to understand.

The project default is:

- enums over generic sum-type machinery
- explicit struct mappings over type-level conversion tools
- named domain types over clever reusable wrappers
- ordinary functions over framework-like abstraction layers

## Good reasons to introduce generics

- repeated conversion between multiple similarly-shaped types
- error accumulation across independent validations
- reusable data transformations that are already duplicated in multiple places
- shared abstractions at architectural seams such as executors, emitters, or repositories

## Bad reasons to introduce generics

- avoiding a ten-line explicit mapping
- hiding a clear domain type behind HLists or type-level programming
- making simple control flow look more abstract
- introducing an abstraction before two or three real call sites justify it

## Prefer explicit enums by default

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReviewDecision {
    Approve,
    RequestChanges(Vec<ReviewIssue>),
    Retry,
}

pub fn describe_decision(decision: ReviewDecision) -> String {
    match decision {
        ReviewDecision::Approve => "approve".to_string(),
        ReviewDecision::RequestChanges(issues) => {
            format!("request-changes:{}", issues.len())
        }
        ReviewDecision::Retry => "retry".to_string(),
    }
}
```

Use generic sum-type machinery only when an ordinary enum stops being maintainable for a proven reason.

## Prefer explicit struct conversion by default

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PromptTemplateInput {
    pub prompt: String,
    pub plan: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PromptRenderInput {
    pub prompt: String,
    pub plan: String,
}

pub fn to_render_input(input: PromptTemplateInput) -> PromptRenderInput {
    PromptRenderInput {
        prompt: input.prompt,
        plan: input.plan,
    }
}
```

If the mapping is short and obvious, keep it explicit.

## When `frunk` is appropriate

Use `frunk` as a targeted tool, not as the default style of the codebase.

The docs for `frunk` show these capabilities clearly:

- `LabelledGeneric` and `labelled_convert_from` for shape-compatible named-field conversions
- `transform_from` and `Transmogrifier` for recursively similar shapes
- `Validated` for accumulating independent validation errors into an HList-backed success value

### `LabelledGeneric` for repeated shape-compatible conversions

Use it when:

- several structs have the same named fields
- manual mappings are repeated often enough to become noise
- the conversion is mechanical rather than policy-heavy

```rust
use frunk::LabelledGeneric;

#[derive(Debug, Clone, PartialEq, Eq, LabelledGeneric)]
pub struct PlanApiModel {
    pub prompt: String,
    pub plan: String,
}

#[derive(Debug, Clone, PartialEq, Eq, LabelledGeneric)]
pub struct PlanDomainModel {
    pub prompt: String,
    pub plan: String,
}

pub fn to_domain(model: PlanApiModel) -> PlanDomainModel {
    frunk::labelled_convert_from(model)
}
```

Do not use it when the conversion embeds domain policy, validation, or renaming logic that deserves explicit code.

### `transform_from` for field-order mismatch or subset conversion

Use it when the destination is still the same conceptual shape but with reordered or subset fields.

```rust
use frunk::LabelledGeneric;

#[derive(Debug, Clone, PartialEq, Eq, LabelledGeneric)]
pub struct FullReviewRecord {
    pub summary: String,
    pub issue_count: u32,
    pub reviewer: String,
}

#[derive(Debug, Clone, PartialEq, Eq, LabelledGeneric)]
pub struct ReviewSummary {
    pub reviewer: String,
    pub summary: String,
}

pub fn summarize(record: FullReviewRecord) -> ReviewSummary {
    frunk::transform_from(record)
}
```

Do not use it to hide real modeling differences. If the source and target have meaningfully different concepts, write the mapping explicitly.

### `Transmogrifier` for recursively similar shapes

Use it when nested structs are mechanically related and explicit conversion would become repetitive.

```rust
use frunk::LabelledGeneric;
use frunk::labelled::Transmogrifier;

#[derive(Debug, Clone, PartialEq, Eq, LabelledGeneric)]
pub struct InternalLimits {
    pub retry_limit: u32,
    pub continuation_budget: u32,
    pub debug_notes: String,
}

#[derive(Debug, Clone, PartialEq, Eq, LabelledGeneric)]
pub struct InternalConfig {
    pub name: String,
    pub limits: InternalLimits,
    pub internal_only: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, LabelledGeneric)]
pub struct PublicLimits {
    pub retry_limit: u32,
    pub continuation_budget: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, LabelledGeneric)]
pub struct PublicConfig {
    pub name: String,
    pub limits: PublicLimits,
}

pub fn public_view(config: InternalConfig) -> PublicConfig {
    config.transmogrify()
}
```

Use this sparingly. The more policy or normalization a conversion contains, the less suitable a recursive structural conversion becomes.

### `Validated` for independent validation

Use `Validated` when several checks can run independently and the caller benefits from seeing all failures at once.

```rust
use frunk::prelude::*;
use frunk::{Validated, hlist_pat};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RawUserInput {
    pub name: String,
    pub email: String,
    pub age: u8,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Name(String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Email(String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Age(u8);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct User {
    pub name: Name,
    pub email: Email,
    pub age: Age,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InputError {
    EmptyName,
    InvalidEmail,
    TooYoung,
}

pub fn validate_name(input: &str) -> Result<Name, Vec<InputError>> {
    (!input.trim().is_empty())
        .then(|| Name(input.to_string()))
        .ok_or(vec![InputError::EmptyName])
}

pub fn validate_email(input: &str) -> Result<Email, Vec<InputError>> {
    input
        .contains('@')
        .then(|| Email(input.to_string()))
        .ok_or(vec![InputError::InvalidEmail])
}

pub fn validate_age(input: u8) -> Result<Age, Vec<InputError>> {
    (input >= 18)
        .then_some(Age(input))
        .ok_or(vec![InputError::TooYoung])
}

pub fn validate_user(input: &RawUserInput) -> Result<User, Vec<InputError>> {
    let validated: Validated<HList!(Name, Email, Age), Vec<InputError>> =
        validate_name(&input.name).into_validated()
        + validate_email(&input.email)
        + validate_age(input.age);

    validated.into_result().map(|hlist_pat!(name, email, age)| User {
        name,
        email,
        age,
    })
}
```

Do not use `Validated` for sequential checks where later validation depends on earlier output. In that case, plain `Result` plus `?` is the right tool.

## Prefer traits at boundaries, not in domain modeling by default

Traits are excellent for capabilities such as executors and emitters.

```rust
pub trait ReviewExecutor {
    fn run_review(&self, request: &ReviewRequest) -> Result<ReviewOutput, ReviewExecutionError>;
}

pub trait RunLogger {
    fn record(&self, event: &RunLogEvent);
}
```

That is different from introducing generic traits for domain entities with only one implementation. Prefer concrete types until multiple implementations are real and useful.

## Smells

- HLists or type-level labels in routine domain code with one call site
- `frunk` used to avoid a short explicit mapping
- abstract traits for domain concepts that only have one implementation and no variation pressure
- generic wrappers whose names hide the real business meaning
