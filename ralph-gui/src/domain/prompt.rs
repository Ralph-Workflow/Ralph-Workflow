//! Pure domain helpers for prompt operations.
//!
//! Policy decisions about prompts belong here, not in boundary modules.

use serde::Deserialize;

/// Decision about whether to perform a live review or return a dry-run result.
#[derive(Debug, Clone)]
pub enum ReviewDecision {
    /// Return a dry-run placeholder without making an API call.
    DryRun(DryRunReviewResult),
    /// Proceed with a live API call using the provided API key.
    Live { api_key: String },
}

/// Determine whether to use dry-run mode or proceed with a live API call.
///
/// This is pure domain policy - the boundary only gathers the flag value.
pub fn determine_review_decision(dry_run_flag: bool, api_key: Option<String>) -> ReviewDecision {
    if dry_run_flag {
        return ReviewDecision::DryRun(dry_run_review_result());
    }

    // Check if API key is available
    match api_key {
        Some(key) if !key.is_empty() => ReviewDecision::Live { api_key: key },
        _ => ReviewDecision::DryRun(dry_run_review_result()),
    }
}

/// Result of AI-assisted prompt review (dry-run placeholder).
#[derive(Debug, Clone)]
pub struct DryRunReviewResult {
    pub suggestions: Vec<String>,
    pub improved_prompt: Option<String>,
}

/// Return a dry-run placeholder for prompt review.
pub fn dry_run_review_result() -> DryRunReviewResult {
    DryRunReviewResult {
        suggestions: vec![
            "Ensure acceptance criteria are specific and testable.".to_string(),
            "Add an 'Out of Scope' section to prevent scope creep.".to_string(),
            "Specify which files or modules should be modified.".to_string(),
        ],
        improved_prompt: None,
    }
}

/// Result of AI-assisted prompt description (dry-run placeholder).
pub fn dry_run_describe_result(description: &str) -> String {
    format!(
        "# Task: {description}\n\n## Objective\n\nImplement the requested feature.\n\n## Acceptance Criteria\n\n- [ ] Feature works as described\n- [ ] Tests are added\n- [ ] Documentation is updated\n"
    )
}

/// A parsed template with metadata extracted from content.
#[derive(Debug, Clone)]
pub struct ParsedTemplate {
    pub name: String,
    pub description: String,
    pub content: String,
}

/// Parse template metadata from file content and path.
///
/// Extracts the first line as description if it starts with '#'.
pub fn parse_template(name: &str, content: &str) -> ParsedTemplate {
    let description = content
        .lines()
        .next()
        .filter(|l| l.starts_with('#'))
        .map(|l| l.trim_start_matches('#').trim().to_string())
        .unwrap_or_default();

    ParsedTemplate {
        name: name.to_string(),
        description,
        content: content.to_string(),
    }
}

/// Determine if a file should be treated as a template (markdown file).
pub fn is_template_file(path: &std::path::Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .map(|ext| ext == "md")
        .unwrap_or(false)
}

/// Check if dry-run mode is enabled based on environment variable value.
///
/// The env var check is done in the boundary; this just interprets the result.
pub fn is_dry_run(dry_run_flag: bool) -> bool {
    dry_run_flag
}

/// Result type for review parsing.
#[derive(Debug)]
pub enum ReviewParseResult {
    Parsed {
        suggestions: Vec<String>,
        improved_prompt: Option<String>,
    },
    Fallback(String),
}

impl ReviewParseResult {
    /// Returns the suggestions if parsed, or None if fallback.
    pub fn suggestions(&self) -> Option<&Vec<String>> {
        match self {
            ReviewParseResult::Parsed { suggestions, .. } => Some(suggestions),
            ReviewParseResult::Fallback(_) => None,
        }
    }

    /// Returns the fallback text if fallback, or None if parsed.
    pub fn fallback_text(&self) -> Option<&str> {
        match self {
            ReviewParseResult::Fallback(s) => Some(s),
            ReviewParseResult::Parsed { .. } => None,
        }
    }

    /// Returns the improved prompt if parsed, or None if fallback.
    pub fn improved_prompt(&self) -> Option<Option<&str>> {
        match self {
            ReviewParseResult::Parsed {
                improved_prompt, ..
            } => Some(improved_prompt.as_ref().map(|x| x.as_str())),
            ReviewParseResult::Fallback(_) => None,
        }
    }
}

/// Parse the API response text into a review result.
pub fn parse_review_response(text: &str) -> ReviewParseResult {
    serde_json::from_str::<PromptReviewResultStruct>(text)
        .map(|r| ReviewParseResult::Parsed {
            suggestions: r.suggestions,
            improved_prompt: r.improved_prompt,
        })
        .unwrap_or_else(|_| ReviewParseResult::Fallback(text.to_string()))
}

/// Internal struct for parsing the review API response.
#[derive(Deserialize)]
struct PromptReviewResultStruct {
    suggestions: Vec<String>,
    improved_prompt: Option<String>,
}

/// Parse the planning drain agent name from TOML config content.
pub fn parse_planning_drain_agent(toml_content: &str) -> Option<String> {
    let parsed: toml::Value = toml::from_str(toml_content).ok()?;

    // Navigate: drains.planning -> chain name -> chains[name][0].agent
    let planning_chain = parsed.get("drains")?.get("planning")?.as_str()?.to_string();

    // Look up the first agent in that chain.
    parsed
        .get("chains")?
        .get(&planning_chain)?
        .as_array()?
        .first()?
        .get("agent")
        .or_else(|| {
            parsed
                .get("chains")?
                .get(&planning_chain)?
                .as_array()?
                .first()?
                .get("name")
        })?
        .as_str()?
        .to_string()
        .into()
}
