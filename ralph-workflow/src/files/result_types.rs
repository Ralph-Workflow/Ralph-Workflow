//! Pure domain data types for structured agent output.
//!
//! This module contains the data types representing parsed artifacts from agent
//! output. These types are populated by JSON artifact parsing (see
//! `reducer::boundary::json_artifact`) and used throughout the pipeline for
//! state transitions and rendering.
//!
//! # Type Families
//!
//! - Plan types: [`PlanElements`], [`Step`], [`StepType`], [`Priority`], etc.
//! - Development result types: [`DevelopmentResultElements`]
//! - Issues types: [`IssuesElements`], [`IssueEntry`]
//! - Fix result types: [`FixResultElements`]
//! - Shared types: [`SkillsMcp`], [`SkillEntry`], [`McpEntry`]

// ===============================================================================
// RICH CONTENT TYPES
// ===============================================================================

/// Inline text element (emphasis, code, link, or plain text)
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InlineElement {
    Text(String),
    Emphasis(String),
    Code(String),
    Link { href: String, text: String },
}

/// Paragraph with mixed inline content
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Paragraph {
    pub content: Vec<InlineElement>,
}

/// Code block with optional language and filename
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CodeBlock {
    pub content: String,
    pub language: Option<String>,
    pub filename: Option<String>,
}

/// Table cell with inline content
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TableCell {
    pub content: Vec<InlineElement>,
}

/// Table row containing cells
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub cells: Vec<TableCell>,
}

/// Table with optional caption and column headers
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Table {
    pub caption: Option<String>,
    pub columns: Vec<String>,
    pub rows: Vec<Row>,
}

/// List type enumeration
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ListType {
    Ordered,
    Unordered,
}

/// List item which can contain inline content and nested lists
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ListItem {
    pub content: Vec<InlineElement>,
    pub nested_list: Option<Box<List>>,
}

/// List container (ordered or unordered)
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct List {
    pub list_type: ListType,
    pub items: Vec<ListItem>,
}

/// Heading with level (2-4)
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Heading {
    pub level: u8,
    pub text: String,
}

/// Rich content element - one of the supported content types
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ContentElement {
    Paragraph(Paragraph),
    CodeBlock(CodeBlock),
    Table(Table),
    List(List),
    Heading(Heading),
}

/// Rich content container holding multiple content elements
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RichContent {
    pub elements: Vec<ContentElement>,
}

// ===============================================================================
// SCOPE AND SUMMARY TYPES
// ===============================================================================

/// Scope item with optional count and category for quantification
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScopeItem {
    pub description: String,
    pub count: Option<String>,
    pub category: Option<String>,
}

/// Plan summary with context and scope items
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanSummary {
    pub context: String,
    pub scope_items: Vec<ScopeItem>,
}

// ===============================================================================
// STEP AND FILE TYPES
// ===============================================================================

/// File action type
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileAction {
    Create,
    Modify,
    Delete,
}

/// Step type enumeration
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum StepType {
    #[default]
    FileChange,
    Action,
    Research,
}

/// Priority level
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Priority {
    Critical,
    High,
    Medium,
    Low,
}

/// Target file in a step
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TargetFile {
    pub path: String,
    pub action: FileAction,
}

/// Implementation step
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Step {
    pub number: u32,
    pub kind: StepType,
    pub priority: Option<Priority>,
    pub title: String,
    pub target_files: Vec<TargetFile>,
    pub location: Option<String>,
    pub rationale: Option<String>,
    pub content: RichContent,
    pub depends_on: Vec<u32>,
}

// ===============================================================================
// CRITICAL FILES TYPES
// ===============================================================================

/// Primary file entry with action and estimated changes
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PrimaryFile {
    pub path: String,
    pub action: FileAction,
    pub estimated_changes: Option<String>,
}

/// Reference file entry with purpose
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReferenceFile {
    pub path: String,
    pub purpose: String,
}

/// Critical files section
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CriticalFiles {
    pub primary_files: Vec<PrimaryFile>,
    pub reference_files: Vec<ReferenceFile>,
}

// ===============================================================================
// RISKS AND VERIFICATION TYPES
// ===============================================================================

/// Severity level
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    Low,
    Medium,
    High,
    Critical,
}

/// Risk-mitigation pair
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RiskPair {
    pub severity: Option<Severity>,
    pub risk: String,
    pub mitigation: String,
}

/// Verification item
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verification {
    pub method: String,
    pub expected_outcome: String,
}

// ===============================================================================
// SKILLS AND MCP RECOMMENDATION TYPES
// ===============================================================================

/// A single skill recommendation entry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SkillEntry {
    /// The skill name (e.g., "frontend-angular", "test-driven-development")
    pub name: String,
    /// Optional reason explaining why this skill is relevant
    pub reason: Option<String>,
}

/// A single MCP recommendation entry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct McpEntry {
    /// The MCP name (e.g., "context7", "angular-mcp")
    pub name: String,
    /// Optional reason explaining why this MCP is relevant
    pub reason: Option<String>,
}

/// Container for skills and MCP recommendations.
///
/// When structured parsing succeeds, `skills` and `mcps` are populated.
/// When content is malformed, `raw_content` preserves the original text
/// so downstream consumers can still extract useful information.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SkillsMcp {
    /// Parsed skill entries
    pub skills: Vec<SkillEntry>,
    /// Parsed MCP entries
    pub mcps: Vec<McpEntry>,
    /// Raw content preserved when parsing is imperfect or mixed content exists
    pub raw_content: Option<String>,
}

// ===============================================================================
// PLAN ELEMENTS (ROOT)
// ===============================================================================

/// Parsed plan elements from a valid plan artifact
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanElements {
    /// The plan summary with context and scope items
    pub summary: PlanSummary,
    /// Implementation steps
    pub steps: Vec<Step>,
    /// Critical files
    pub critical_files: CriticalFiles,
    /// Risks and mitigations
    pub risks_mitigations: Vec<RiskPair>,
    /// Verification strategy
    pub verification_strategy: Vec<Verification>,
    /// Optional skills and MCP recommendations for the next execution agent
    pub skills_mcp: Option<SkillsMcp>,
    /// Optional parallel plan for Phase 4 parallel execution
    pub parallel_plan: Option<ParallelPlanElements>,
}

// ===============================================================================
// PARALLEL PLAN TYPES (RFC-009 Phase 4)
// ===============================================================================

/// Edit area defining what paths a work unit can modify
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EditAreaElements {
    /// Exact file paths allowed
    pub paths: Vec<String>,
    /// Directory prefixes allowed
    pub directories: Vec<String>,
}

/// Work unit in a parallel plan
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkUnitElements {
    /// Unique identifier for the work unit
    pub unit_id: String,
    /// Human-readable description
    pub description: String,
    /// The restricted edit area
    pub edit_area: EditAreaElements,
    /// IDs of work units this depends on
    pub dependencies: Vec<String>,
}

/// Parallel plan containing work units for Phase 4 execution
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParallelPlanElements {
    /// The work units to execute in parallel
    pub work_units: Vec<WorkUnitElements>,
}

// ===============================================================================
// DEVELOPMENT RESULT TYPES
// ===============================================================================

/// Parsed development result elements from a valid development_result artifact.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DevelopmentResultElements {
    /// The development status (required).
    ///
    /// Canonical normalized status value — one of: `"completed"`, `"partial"`, or `"failed"`.
    pub status: String,
    /// Explicit routing decision from the artifact's `decision` field (JSON artifacts only).
    ///
    /// When `Some`, the reducer must use this decision instead of deriving one from `status`.
    /// When `None` (field absent), fall back to status-derived routing.
    pub analysis_decision: Option<crate::reducer::state::DevelopmentAnalysisDecision>,
    /// Summary of what was done (required)
    pub summary: String,
    /// Optional skills and MCP recommendations for the next agent
    pub skills_mcp: Option<SkillsMcp>,
    /// Optional list of files changed
    pub files_changed: Option<String>,
    /// Whether the files changed element was present, even if empty.
    pub files_changed_present: bool,
    /// Optional next steps
    pub next_steps: Option<String>,
    /// Whether the next steps element was present, even if empty.
    pub next_steps_present: bool,
}

impl DevelopmentResultElements {
    /// Returns true if the work is completed.
    #[must_use]
    pub fn is_completed(&self) -> bool {
        self.status == "completed"
    }

    /// Returns true if the work is partially done.
    #[must_use]
    pub fn is_partial(&self) -> bool {
        self.status == "partial"
    }

    /// Returns true if the work failed.
    #[must_use]
    pub fn is_failed(&self) -> bool {
        self.status == "failed"
    }
}

/// Apply the continuation development result contract to parsed elements.
///
/// This validates that when status is `partial` or `failed`, the `next_steps` field
/// is present. Also clears `files_changed` (continuation context doesn't need it).
///
/// Returns the modified elements on success, or an error string on violation.
pub fn apply_continuation_development_result_contract(
    elements: DevelopmentResultElements,
) -> Result<DevelopmentResultElements, String> {
    let normalized = DevelopmentResultElements {
        files_changed: None,
        files_changed_present: false,
        ..elements
    };

    if (normalized.status == "partial" || normalized.status == "failed")
        && normalized.next_steps.is_none()
    {
        return Err(
            "Continuation contract violation: status is 'partial' or 'failed' but \
            'next_steps' field is missing. The agent must provide next steps when \
            reporting partial or failed status in a continuation context.\n\n\
            Example:\n\
            {\n  \"status\": \"partial\",\n  \"summary\": \"...\",\n  \
            \"next_steps\": \"1. ...\"\n}"
                .to_string(),
        );
    }

    Ok(normalized)
}

// ===============================================================================
// ISSUES TYPES
// ===============================================================================

/// A single issue entry with optional skills-mcp recommendations.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IssueEntry {
    /// The issue description text
    pub text: String,
    /// Optional skills and MCP recommendations for fixing this issue
    pub skills_mcp: Option<SkillsMcp>,
}

/// Parsed issues elements from a valid issues artifact.
///
/// Contains either a list of issues or a "no issues found" message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IssuesElements {
    /// List of issues (if any)
    pub issues: Vec<IssueEntry>,
    /// No issues found message (if no issues)
    pub no_issues_found: Option<String>,
}

impl IssuesElements {
    /// Returns true if there are no issues.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.issues.is_empty() && self.no_issues_found.is_some()
    }

    /// Returns the number of issues.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn issue_count(&self) -> usize {
        self.issues.len()
    }

    /// Returns the issue texts as a Vec of Strings for backward-compatible consumers.
    #[must_use]
    pub fn issue_texts(&self) -> Vec<String> {
        self.issues.iter().map(|e| e.text.clone()).collect()
    }
}

// ===============================================================================
// FIX RESULT TYPES
// ===============================================================================

/// Parsed fix result elements from a valid fix_result artifact.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FixResultElements {
    /// The fix status (required).
    ///
    /// Canonical normalized status value — one of: `"all_issues_addressed"`,
    /// `"issues_remain"`, or `"no_issues_found"`.
    pub status: String,
    /// Optional summary of fixes applied
    pub summary: Option<String>,
}

impl FixResultElements {
    /// Returns true if all issues have been addressed or no issues were found.
    #[must_use]
    pub fn is_complete(&self) -> bool {
        self.status == "all_issues_addressed" || self.status == "no_issues_found"
    }

    /// Returns true if issues remain.
    #[must_use]
    pub fn has_remaining_issues(&self) -> bool {
        self.status == "issues_remain"
    }

    /// Returns true if no issues were found.
    #[must_use]
    pub fn is_no_issues(&self) -> bool {
        self.status == "no_issues_found"
    }
}
