// Schema definition and structure types for XSD plan validation.
// Contains all type definitions for plan elements, rich content, and related structures.

use crate::files::llm_output_extraction::xml_helpers::tolerant_parsing::{
    normalize_enum_value, FILE_ACTION_SYNONYMS, LIST_TYPE_SYNONYMS, PRIORITY_SYNONYMS,
    SEVERITY_SYNONYMS, STEP_TYPE_SYNONYMS,
};

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
pub struct Cell {
    pub content: Vec<InlineElement>,
}

/// Table row containing cells
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub cells: Vec<Cell>,
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

impl FileAction {
    pub(super) fn from_str(s: &str) -> Option<Self> {
        const VALID: &[&str] = &["create", "modify", "delete"];
        match normalize_enum_value(s, VALID, FILE_ACTION_SYNONYMS)?.as_str() {
            "create" => Some(Self::Create),
            "modify" => Some(Self::Modify),
            "delete" => Some(Self::Delete),
            _ => None,
        }
    }
}

/// Step type enumeration
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum StepType {
    #[default]
    FileChange,
    Action,
    Research,
}

impl StepType {
    pub(super) fn from_str(s: &str) -> Option<Self> {
        const VALID: &[&str] = &["file-change", "action", "research"];
        match normalize_enum_value(s, VALID, STEP_TYPE_SYNONYMS)?.as_str() {
            "file-change" => Some(Self::FileChange),
            "action" => Some(Self::Action),
            "research" => Some(Self::Research),
            _ => None,
        }
    }
}

/// Priority level
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Priority {
    Critical,
    High,
    Medium,
    Low,
}

impl Priority {
    pub(super) fn from_str(s: &str) -> Option<Self> {
        const VALID: &[&str] = &["critical", "high", "medium", "low"];
        match normalize_enum_value(s, VALID, PRIORITY_SYNONYMS)?.as_str() {
            "critical" => Some(Self::Critical),
            "high" => Some(Self::High),
            "medium" => Some(Self::Medium),
            "low" => Some(Self::Low),
            _ => None,
        }
    }
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

impl Severity {
    pub(super) fn from_str(s: &str) -> Option<Self> {
        const VALID: &[&str] = &["low", "medium", "high", "critical"];
        match normalize_enum_value(s, VALID, SEVERITY_SYNONYMS)?.as_str() {
            "low" => Some(Self::Low),
            "medium" => Some(Self::Medium),
            "high" => Some(Self::High),
            "critical" => Some(Self::Critical),
            _ => None,
        }
    }
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

/// Parsed plan elements from valid XML (v2 - structured)
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
}
