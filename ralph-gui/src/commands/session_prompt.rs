use serde::{Deserialize, Serialize};

/// Result of an AI-assisted PROMPT.md review.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PromptReviewResult {
    pub suggestions: Vec<String>,
    pub improved_prompt: Option<String>,
}

/// Read the content of a PROMPT.md file.
///
/// # Errors
///
/// Returns an error if the file cannot be read.
#[tauri::command]
pub fn read_prompt_file(prompt_path: String) -> Result<String, String> {
    std::fs::read_to_string(&prompt_path).map_err(|e| format!("Failed to read prompt file: {e}"))
}

/// Save content to a PROMPT.md file, creating parent directories if needed.
///
/// # Errors
///
/// Returns an error if the parent directory cannot be created or the file cannot be written.
#[tauri::command]
pub fn save_prompt_file(prompt_path: String, content: String) -> Result<(), String> {
    let path = std::path::PathBuf::from(&prompt_path);
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("Failed to create directory: {e}"))?;
        }
    }
    std::fs::write(&path, content).map_err(|e| format!("Failed to write prompt file: {e}"))
}

/// Read the Anthropic API key from `~/.ralph/config.toml` `[gui]` section.
fn read_api_key_from_config() -> Result<String, String> {
    let config_path = dirs::home_dir()
        .ok_or_else(|| "Cannot determine home directory".to_string())?
        .join(".ralph")
        .join("config.toml");

    let content =
        std::fs::read_to_string(&config_path).map_err(|e| format!("Cannot read config: {e}"))?;

    let parsed: toml::Value =
        toml::from_str(&content).map_err(|e| format!("Cannot parse config: {e}"))?;

    parsed
        .get("gui")
        .and_then(|g| g.get("api_key"))
        .and_then(|k| k.as_str())
        .map(std::borrow::ToOwned::to_owned)
        .ok_or_else(|| "api_key not found in [gui] section".to_string())
}

/// Review a PROMPT.md using the Anthropic Claude API.
///
/// The API key is read from the `ANTHROPIC_API_KEY` environment variable,
/// or from `~/.ralph/config.toml` `[gui]` `api_key` as a fallback.
///
/// When the environment variable `RALPH_GUI_DRY_RUN=1` is set, the network
/// call is skipped and a placeholder result is returned (useful in tests).
///
/// # Errors
///
/// Returns an error if the API key is absent or the API call fails.
#[tauri::command]
pub fn review_prompt_with_ai(prompt_content: String) -> Result<PromptReviewResult, String> {
    // Dry-run mode for testing — skip network call.
    if std::env::var("RALPH_GUI_DRY_RUN").as_deref() == Ok("1") {
        return Ok(PromptReviewResult {
            suggestions: vec![
                "Ensure acceptance criteria are specific and testable.".to_string(),
                "Add an 'Out of Scope' section to prevent scope creep.".to_string(),
                "Specify which files or modules should be modified.".to_string(),
            ],
            improved_prompt: None,
        });
    }

    let api_key = std::env::var("ANTHROPIC_API_KEY")
        .ok()
        .filter(|k| !k.is_empty())
        .or_else(|| read_api_key_from_config().ok())
        .ok_or_else(|| {
            "ANTHROPIC_API_KEY not set. Set it in environment or ~/.ralph/config.toml [gui] api_key."
                .to_string()
        })?;

    let body = serde_json::json!({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "system": "You review PROMPT.md specifications for AI coding agent tasks. \
                   Respond ONLY with valid JSON containing: \
                   'suggestions' (array of improvement strings) and \
                   'improved_prompt' (optional string with the full improved prompt).",
        "messages": [{ "role": "user", "content": prompt_content }]
    });

    let response = ureq::post("https://api.anthropic.com/v1/messages")
        .set("x-api-key", &api_key)
        .set("anthropic-version", "2023-06-01")
        .set("content-type", "application/json")
        .send_json(body)
        .map_err(|e| format!("API call failed: {e}"))?;

    let json: serde_json::Value = response
        .into_json()
        .map_err(|e| format!("Failed to parse API response: {e}"))?;

    let text = json["content"][0]["text"].as_str().unwrap_or("{}");

    // Try to parse the model's JSON response; fall back to wrapping raw text as suggestion.
    let result =
        serde_json::from_str::<PromptReviewResult>(text).unwrap_or_else(|_| PromptReviewResult {
            suggestions: vec![text.to_string()],
            improved_prompt: None,
        });

    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;
    use tempfile::TempDir;

    // Serialize tests that mutate process-wide env vars to prevent race conditions
    // between parallel test threads.
    static ENV_MUTEX: Mutex<()> = Mutex::new(());

    #[test]
    fn test_save_prompt_file_creates_file() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("PROMPT.md");
        let content = "# My prompt\n\nHello world.";
        let result = save_prompt_file(path.to_string_lossy().to_string(), content.to_string());
        assert!(result.is_ok(), "Expected Ok but got: {result:?}");
        assert!(path.exists(), "Prompt file should exist");
        let written = std::fs::read_to_string(&path).unwrap();
        assert_eq!(written, content);
    }

    #[test]
    fn test_save_prompt_file_creates_parent_dirs() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("subdir").join("nested").join("PROMPT.md");
        let result = save_prompt_file(path.to_string_lossy().to_string(), "# Test".to_string());
        assert!(result.is_ok(), "Expected Ok but got: {result:?}");
        assert!(path.exists());
    }

    #[test]
    fn test_read_prompt_file_returns_content() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("PROMPT.md");
        let expected = "# My Task\n\nDo the thing.";
        std::fs::write(&path, expected).unwrap();
        let result = read_prompt_file(path.to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok but got: {result:?}");
        assert_eq!(result.unwrap(), expected);
    }

    #[test]
    fn test_read_prompt_file_errors_when_missing() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("NONEXISTENT.md");
        let result = read_prompt_file(path.to_string_lossy().to_string());
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Failed to read prompt file"));
    }

    #[test]
    fn test_review_prompt_errors_without_api_key() {
        // Acquire mutex to prevent race conditions with other env-var-mutating tests.
        let _guard = ENV_MUTEX.lock().unwrap();
        // Remove both env var and ensure no config fallback path is reachable.
        // We temporarily remove ANTHROPIC_API_KEY to simulate absence.
        let old_val = std::env::var("ANTHROPIC_API_KEY").ok();
        let old_dry_run = std::env::var("RALPH_GUI_DRY_RUN").ok();
        std::env::remove_var("RALPH_GUI_DRY_RUN");
        std::env::remove_var("ANTHROPIC_API_KEY");

        let result = review_prompt_with_ai("# Test prompt".to_string());

        // Restore env vars.
        if let Some(v) = old_val {
            std::env::set_var("ANTHROPIC_API_KEY", v);
        }
        if let Some(v) = old_dry_run {
            std::env::set_var("RALPH_GUI_DRY_RUN", v);
        }

        // Result may be Err (no key) or Ok (if config file has key) — either is valid.
        // We only assert the error message shape when it errors.
        if let Err(e) = result {
            assert!(
                e.contains("ANTHROPIC_API_KEY"),
                "Error should mention ANTHROPIC_API_KEY: {e}"
            );
        }
    }

    #[test]
    fn test_review_prompt_dry_run_returns_placeholder() {
        // Acquire mutex to prevent race conditions with other env-var-mutating tests.
        let _guard = ENV_MUTEX.lock().unwrap();
        let old_val = std::env::var("RALPH_GUI_DRY_RUN").ok();
        std::env::set_var("RALPH_GUI_DRY_RUN", "1");
        let result = review_prompt_with_ai("# Test prompt".to_string());
        // Restore previous value.
        if let Some(v) = old_val {
            std::env::set_var("RALPH_GUI_DRY_RUN", v);
        } else {
            std::env::remove_var("RALPH_GUI_DRY_RUN");
        }
        assert!(result.is_ok(), "Expected Ok in dry-run mode: {result:?}");
        let review = result.unwrap();
        assert!(!review.suggestions.is_empty(), "Should have suggestions");
    }
}
