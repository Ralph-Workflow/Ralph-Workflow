mod tests {
    use super::*;
    use crate::phases::commit::io::diff_truncation::{
        truncate_diff_if_large, truncate_lines_to_fit,
        CLAUDE_MAX_PROMPT_SIZE, GLM_MAX_PROMPT_SIZE, MAX_SAFE_PROMPT_SIZE,
    };

    #[test]
    fn test_truncate_diff_if_large() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let large_diff = "diff --git a/src/main.rs b/src/main.rs\n".repeat(1000);
        let truncated = truncate_diff_if_large(&large_diff, 10_000);

        assert!(truncated.len() <= 10_000 + 200);
        assert!(truncated.contains("[Truncated:"));
    }

    #[test]
    fn test_truncate_diff_no_truncation_needed() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let small_diff = "diff --git a/src/main.rs b/src/main.rs\n+change\n";
        let truncated = truncate_diff_if_large(small_diff, 10_000);

        assert_eq!(truncated, small_diff);
    }

    #[test]
    fn test_truncate_diff_preserves_structure() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let diff = "diff --git a/src/main.rs b/src/main.rs\n+change1\n\
            diff --git a/src/lib.rs b/src/lib.rs\n+change2\n";
        let truncated = truncate_diff_if_large(diff, 10_000);

        assert!(truncated.contains("diff --git a/src/main.rs"));
        assert!(truncated.contains("diff --git a/src/lib.rs"));
    }

    #[test]
    fn test_truncate_diff_very_small_limit() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let diff = "diff --git a/src/main.rs b/src/main.rs\n+change\n";
        let truncated = truncate_diff_if_large(diff, 80);

        assert!(truncated.len() <= 80 + 200);
        assert!(truncated.contains("[Truncated:"));
    }

    #[test]
    fn test_truncate_lines_to_fit() {
        let lines = vec!["line1".to_string(), "line2".to_string(), "line3".to_string()];
        let max_size = 12;

        let truncated = truncate_lines_to_fit(&lines, max_size);

        assert!(truncated.join("\n").len() <= max_size);
    }

    #[test]
    fn test_truncate_lines_to_fit_no_truncation() {
        let lines = vec!["a".to_string(), "b".to_string()];
        let max_size = 100;

        let truncated = truncate_lines_to_fit(&lines, max_size);

        assert_eq!(truncated.len(), 2);
    }

    #[test]
    fn test_effective_model_budget_bytes_single_agent() {
        let agents = vec!["claude".to_string()];
        assert_eq!(effective_model_budget_bytes(&agents), CLAUDE_MAX_PROMPT_SIZE);
    }

    #[test]
    fn test_effective_model_budget_bytes_multiple_agents() {
        let agents = vec!["claude".to_string(), "glm".to_string()];
        assert_eq!(effective_model_budget_bytes(&agents), GLM_MAX_PROMPT_SIZE);
    }

    #[test]
    fn test_effective_model_budget_bytes_no_agents() {
        let agents: Vec<String> = vec![];
        assert_eq!(effective_model_budget_bytes(&agents), MAX_SAFE_PROMPT_SIZE);
    }
}
