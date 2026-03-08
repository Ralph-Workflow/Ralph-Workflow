mod tests {
    use super::*;
    use crate::agents::AgentRegistry;
    use crate::checkpoint::execution_history::ExecutionHistory;
    use crate::checkpoint::RunContext;
    use crate::config::Config;
    use crate::executor::{MockProcessExecutor, ProcessExecutor};
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::prompts::template_registry::TemplateRegistry;
    use crate::workspace::MemoryWorkspace;
    use std::collections::HashMap;
    use std::fs;
    use std::path::PathBuf;
    use std::sync::Arc;
    use tempfile::tempdir;

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
        let large_diff = "diff --git a/src/main.rs b/src/main.rs\n".repeat(100);
        let truncated = truncate_diff_if_large(&large_diff, 50);

        assert!(truncated.len() <= 100);
        assert!(truncated.contains("diff --git"));
    }

    #[test]
    fn test_truncate_keeps_high_priority_files() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let diff = "diff --git a/README.md b/README.md\n\
            +doc change\n\
            diff --git a/src/main.rs b/src/main.rs\n\
            +important change\n\
            diff --git a/tests/test.rs b/tests/test.rs\n\
            +test change\n";

        let truncated = truncate_diff_if_large(diff, 80);
        assert!(truncated.contains("src/main.rs"));
    }

    #[test]
    fn test_truncate_lines_to_fit() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let lines = vec![
            "line1".to_string(),
            "line2".to_string(),
            "line3".to_string(),
            "line4".to_string(),
        ];

        let max_size = 18;
        let truncated = truncate_lines_to_fit(&lines, max_size);

        assert!(!truncated.is_empty());
        assert!(
            truncated
                .last()
                .is_some_and(|l| l.ends_with("[truncated...]")),
            "expected last line to be marked as truncated"
        );
        let total_size: usize = truncated.iter().map(|l| l.len() + 1).sum();
        assert!(
            total_size <= max_size,
            "truncate_lines_to_fit must respect max size after suffix"
        );
    }

    #[test]
    fn test_extract_commit_message_from_file_reads_primary_xml() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let workspace = MemoryWorkspace::new_test().with_file(
            ".agent/tmp/commit_message.xml",
            "<ralph-commit><ralph-subject>feat: add</ralph-subject></ralph-commit>",
        );

        let extraction = extract_commit_message_from_file_with_workspace(&workspace);
        let CommitExtractionOutcome::Valid(extracted) = extraction else {
            panic!("expected extraction");
        };
        assert_eq!(extracted.into_message(), "feat: add");
    }

    #[test]
    fn test_extract_commit_message_from_file_ignores_processed_archive() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let workspace = MemoryWorkspace::new_test().with_file(
            ".agent/tmp/commit_message.xml.processed",
            "<ralph-commit><ralph-subject>feat: add</ralph-subject></ralph-commit>",
        );

        let extraction = extract_commit_message_from_file_with_workspace(&workspace);
        assert!(matches!(
            extraction,
            CommitExtractionOutcome::MissingFile(_)
        ));
    }

    #[test]
    fn test_run_commit_attempt_uses_unique_logfile_with_attempt_suffix() {
        let cloud = crate::config::types::CloudConfig::disabled();
        let workspace = MemoryWorkspace::new_test().with_file(
            xml_paths::COMMIT_MESSAGE_XML,
            "<ralph-commit><ralph-subject>feat: x</ralph-subject></ralph-commit>",
        );
        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let mut timer = Timer::new();

        let config = Config::default();
        let registry = AgentRegistry::new().unwrap();
        let template_context = TemplateContext::default();

        let executor = Arc::new(
            MockProcessExecutor::new()
                .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
        );
        let executor_arc: Arc<dyn ProcessExecutor> = executor.clone();

        let repo_root = PathBuf::from("/mock/repo");
        let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
        let mut ctx = PhaseContext {
            config: &config,
            registry: &registry,
            logger: &logger,
            colors: &colors,
            timer: &mut timer,
            developer_agent: "claude",
            reviewer_agent: "claude",
            review_guidelines: None,
            template_context: &template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            prompt_history: HashMap::new(),
            executor: executor_arc.as_ref(),
            executor_arc: executor_arc.clone(),
            repo_root: repo_root.as_path(),
            workspace: &workspace,
            workspace_arc: std::sync::Arc::new(workspace.clone()),
            run_log_context: &run_log_context,
            cloud_reporter: None,
            cloud: &cloud,
        };

        let _ = run_commit_attempt(&mut ctx, 2, "diff --git a/a b/a\n+change\n", "claude")
            .expect("run_commit_attempt should succeed");

        let calls = executor.agent_calls();
        assert_eq!(calls.len(), 1);
        // New per-run log format: .agent/logs-<run_id>/agents/commit_2.log
        // Agent identity is in the log file header, not the filename
        assert!(
            calls[0].logfile.contains("/agents/commit_2.log"),
            "commit generation log should use per-run format with phase_index naming: {}",
            calls[0].logfile
        );
    }

    #[test]
    fn test_run_commit_attempt_logs_diff_truncated_when_model_safe_diff_contains_marker() {
        let cloud = crate::config::types::CloudConfig::disabled();
        let workspace = MemoryWorkspace::new_test().with_file(
            xml_paths::COMMIT_MESSAGE_XML,
            "<ralph-commit><ralph-subject>feat: x</ralph-subject></ralph-commit>",
        );
        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let mut timer = Timer::new();

        let config = Config::default();
        let registry = AgentRegistry::new().unwrap();
        let template_context = TemplateContext::default();

        let executor = Arc::new(
            MockProcessExecutor::new()
                .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
        );
        let executor_arc: Arc<dyn ProcessExecutor> = executor;

        let repo_root = PathBuf::from("/mock/repo");
        let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
        let mut ctx = PhaseContext {
            config: &config,
            registry: &registry,
            logger: &logger,
            colors: &colors,
            timer: &mut timer,
            developer_agent: "claude",
            reviewer_agent: "claude",
            review_guidelines: None,
            template_context: &template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            prompt_history: HashMap::new(),
            executor: executor_arc.as_ref(),
            executor_arc: executor_arc.clone(),
            repo_root: repo_root.as_path(),
            workspace: &workspace,
            workspace_arc: std::sync::Arc::new(workspace.clone()),
            run_log_context: &run_log_context,
            cloud_reporter: None,
            cloud: &cloud,
        };

        let model_safe_diff = "diff --git a/a b/a\n+change\n\n[Truncated: 1 of 2 files shown]\n";
        let _ = run_commit_attempt(&mut ctx, 1, model_safe_diff, "claude")
            .expect("run_commit_attempt should succeed");

        let log_files = workspace.list_files_in_dir(".agent/logs/commit_generation");
        let attempt_log_path = log_files
            .iter()
            .find(|p| {
                let path = p.to_string_lossy();
                path.ends_with(".log") && path.contains("attempt_") && path.contains("run_")
            })
            .expect("expected an attempt log file to be written")
            .to_string_lossy()
            .to_string();

        let log_content = workspace
            .get_file(&attempt_log_path)
            .expect("attempt log should be readable");
        assert!(
            log_content.contains("Diff truncated: YES"),
            "expected truncation marker in log, got:\n{log_content}"
        );
    }

    #[test]
    fn test_run_commit_attempt_prompt_key_is_cycle_scoped_prevents_stale_prompt_replay() {
        // Regression: commit prompt replay keys must not collide across commit cycles.
        // If prompt_history contains a stale prompt under the legacy attempt-only key,
        // we must generate a fresh prompt for the current diff instead of replaying.

        let cloud = crate::config::types::CloudConfig::disabled();
        let workspace = MemoryWorkspace::new_test().with_file(
            xml_paths::COMMIT_MESSAGE_XML,
            "<ralph-commit><ralph-subject>feat: x</ralph-subject></ralph-commit>",
        );
        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let mut timer = Timer::new();

        let config = Config::default();
        let registry = AgentRegistry::new().unwrap();
        let template_context = TemplateContext::default();

        let executor = Arc::new(
            MockProcessExecutor::new()
                .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
        );
        let executor_arc: Arc<dyn ProcessExecutor> = executor;

        let repo_root = PathBuf::from("/mock/repo");
        let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();

        let mut prompt_history = HashMap::new();
        prompt_history.insert(
            "commit_message_attempt_1".to_string(),
            "STALE_MARKER: this prompt should not be replayed".to_string(),
        );

        // Simulate a later commit cycle where attempt resets to 1.
        let run_context = RunContext::new().with_developer_runs(2);

        let mut ctx = PhaseContext {
            config: &config,
            registry: &registry,
            logger: &logger,
            colors: &colors,
            timer: &mut timer,
            developer_agent: "claude",
            reviewer_agent: "claude",
            review_guidelines: None,
            template_context: &template_context,
            run_context,
            execution_history: ExecutionHistory::new(),
            prompt_history,
            executor: executor_arc.as_ref(),
            executor_arc: executor_arc.clone(),
            repo_root: repo_root.as_path(),
            workspace: &workspace,
            workspace_arc: std::sync::Arc::new(workspace.clone()),
            run_log_context: &run_log_context,
            cloud_reporter: None,
            cloud: &cloud,
        };

        let fresh_diff = "diff --git a/a b/a\n+FRESH_DIFF_CONTENT\n";
        let _ = run_commit_attempt(&mut ctx, 1, fresh_diff, "claude")
            .expect("run_commit_attempt should succeed");

        let saved_prompt = workspace
            .read(config.prompt_path.as_path())
            .expect("prompt should be written");
        assert!(
            !saved_prompt.contains("STALE_MARKER"),
            "expected stale prompt not to be replayed; prompt was:\n{saved_prompt}"
        );
        assert!(
            saved_prompt.contains("FRESH_DIFF_CONTENT"),
            "expected prompt to include the fresh diff content; prompt was:\n{saved_prompt}"
        );
    }

    #[test]
    fn test_run_commit_attempt_errors_on_missing_template_variables() {
        let cloud = crate::config::types::CloudConfig::disabled();
        let tempdir = tempdir().expect("create temp dir");
        let template_path = tempdir.path().join("commit_message_xml.txt");
        fs::write(
            &template_path,
            "Commit {{DIFF}}\nMissing: {{MISSING}}\n{{COMMIT_MESSAGE_XML_PATH}}\n{{COMMIT_MESSAGE_XSD_PATH}}\n",
        )
        .expect("write commit template");

        let workspace = MemoryWorkspace::new_test();
        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let mut timer = Timer::new();

        let config = Config::default();
        let registry = AgentRegistry::new().unwrap();
        let template_context =
            TemplateContext::new(TemplateRegistry::new(Some(tempdir.path().to_path_buf())));

        let executor = Arc::new(MockProcessExecutor::new());
        let executor_arc: Arc<dyn ProcessExecutor> = executor.clone();

        let repo_root = PathBuf::from("/mock/repo");
        let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
        let mut ctx = PhaseContext {
            config: &config,
            registry: &registry,
            logger: &logger,
            colors: &colors,
            timer: &mut timer,
            developer_agent: "claude",
            reviewer_agent: "claude",
            review_guidelines: None,
            template_context: &template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            prompt_history: HashMap::new(),
            executor: executor_arc.as_ref(),
            executor_arc: executor_arc.clone(),
            repo_root: repo_root.as_path(),
            workspace: &workspace,
            workspace_arc: std::sync::Arc::new(workspace.clone()),
            run_log_context: &run_log_context,
            cloud_reporter: None,
            cloud: &cloud,
        };

        let err = run_commit_attempt(&mut ctx, 1, "diff --git a/a b/a\n+change\n", "claude")
            .err()
            .expect("missing template variables should fail validation");
        assert!(
            err.to_string().contains("unresolved placeholders"),
            "expected unresolved placeholder error, got: {err}"
        );
        assert!(
            executor.agent_calls().is_empty(),
            "agent should not be invoked when template variables are missing"
        );
    }

    #[test]
    fn test_generate_commit_message_with_chain_treats_skip_as_success() {
        // Regression: legacy plumbing should treat <ralph-skip> as a successful outcome,
        // and represent it explicitly so callers cannot accidentally commit an empty message.
        let workspace = MemoryWorkspace::new_test().with_file(
            xml_paths::COMMIT_MESSAGE_XML,
            "<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>",
        );

        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let mut timer = Timer::new();

        let config = Config::default();
        let registry = AgentRegistry::new().unwrap();
        let template_context = TemplateContext::default();

        let executor = Arc::new(
            MockProcessExecutor::new()
                .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
        );
        let executor_arc: Arc<dyn ProcessExecutor> = executor;
        let executor_ref: &dyn ProcessExecutor = executor_arc.as_ref();
        let executor_arc_for_runtime: Arc<dyn ProcessExecutor> = executor_arc.clone();

        let mut runtime = PipelineRuntime {
            timer: &mut timer,
            logger: &logger,
            colors: &colors,
            config: &config,
            executor: executor_ref,
            executor_arc: executor_arc_for_runtime,
            workspace: &workspace,
            workspace_arc: std::sync::Arc::new(workspace.clone()),
        };

        let agents = vec!["claude".to_string()];
        let result = generate_commit_message_with_chain(
            "diff --git a/a b/a\n+change\n",
            &registry,
            &mut runtime,
            &agents,
            &template_context,
            &workspace,
            &HashMap::new(),
        )
        .expect("expected skip outcome to be treated as success");

        match result.outcome {
            CommitMessageOutcome::Skipped { reason } => {
                assert_eq!(reason, "No changes found");
            }
            other @ CommitMessageOutcome::Message(_) => {
                panic!("expected skipped outcome, got: {other:?}")
            }
        }
    }

    #[test]
    fn test_generate_commit_message_prompt_key_is_diff_scoped_prevents_stale_prompt_replay() {
        // Regression: single-agent commit generation must not use a constant replay key.
        // A stale prompt under the legacy key must not be replayed for a new diff.

        let workspace = MemoryWorkspace::new_test().with_file(
            xml_paths::COMMIT_MESSAGE_XML,
            "<ralph-commit><ralph-subject>feat: x</ralph-subject></ralph-commit>",
        );
        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let mut timer = Timer::new();

        let config = Config::default();
        let registry = AgentRegistry::new().unwrap();
        let template_context = TemplateContext::default();

        let executor = Arc::new(
            MockProcessExecutor::new()
                .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
        );
        let executor_arc: Arc<dyn ProcessExecutor> = executor;

        let executor_ref: &dyn ProcessExecutor = executor_arc.as_ref();
        let executor_arc_for_runtime: Arc<dyn ProcessExecutor> = executor_arc.clone();
        let mut runtime = PipelineRuntime {
            timer: &mut timer,
            logger: &logger,
            colors: &colors,
            config: &config,
            executor: executor_ref,
            executor_arc: executor_arc_for_runtime,
            workspace: &workspace,
            workspace_arc: std::sync::Arc::new(workspace.clone()),
        };

        let mut prompt_history = HashMap::new();
        prompt_history.insert(
            "commit_message_attempt_1".to_string(),
            "STALE_MARKER: constant-key prompt".to_string(),
        );

        let fresh_diff = "diff --git a/a b/a\n+FRESH_DIFF_CONTENT\n";
        let _ = generate_commit_message(
            fresh_diff,
            &registry,
            &mut runtime,
            "claude",
            &template_context,
            &workspace,
            &prompt_history,
        )
        .expect("expected commit generation to succeed");

        let saved_prompt = workspace
            .read(config.prompt_path.as_path())
            .expect("prompt should be written");
        assert!(
            !saved_prompt.contains("STALE_MARKER"),
            "expected stale prompt not to be replayed; prompt was:\n{saved_prompt}"
        );
        assert!(
            saved_prompt.contains("FRESH_DIFF_CONTENT"),
            "expected prompt to include the fresh diff content; prompt was:\n{saved_prompt}"
        );
    }

    #[test]
    fn test_generate_commit_message_with_chain_prompt_keys_are_diff_scoped_prevents_stale_prompt_replay(
    ) {
        // Regression: fallback-chain commit generation keys must not restart at 1 each cycle.
        // A stale prompt under the legacy chain key must not be replayed for a new diff.

        let workspace = MemoryWorkspace::new_test().with_file(
            xml_paths::COMMIT_MESSAGE_XML,
            "<ralph-commit><ralph-subject>feat: x</ralph-subject></ralph-commit>",
        );
        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let mut timer = Timer::new();

        let config = Config::default();
        let registry = AgentRegistry::new().unwrap();
        let template_context = TemplateContext::default();

        let executor = Arc::new(
            MockProcessExecutor::new()
                .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
        );
        let executor_arc: Arc<dyn ProcessExecutor> = executor;
        let executor_ref: &dyn ProcessExecutor = executor_arc.as_ref();
        let executor_arc_for_runtime: Arc<dyn ProcessExecutor> = executor_arc.clone();
        let mut runtime = PipelineRuntime {
            timer: &mut timer,
            logger: &logger,
            colors: &colors,
            config: &config,
            executor: executor_ref,
            executor_arc: executor_arc_for_runtime,
            workspace: &workspace,
            workspace_arc: std::sync::Arc::new(workspace.clone()),
        };

        let agents = vec!["claude".to_string()];

        let mut prompt_history = HashMap::new();
        prompt_history.insert(
            "commit_message_chain_attempt_1".to_string(),
            "STALE_MARKER: chain prompt".to_string(),
        );

        let fresh_diff = "diff --git a/a b/a\n+FRESH_DIFF_CONTENT\n";
        let _ = generate_commit_message_with_chain(
            fresh_diff,
            &registry,
            &mut runtime,
            &agents,
            &template_context,
            &workspace,
            &prompt_history,
        )
        .expect("expected commit generation with chain to succeed");

        let saved_prompt = workspace
            .read(config.prompt_path.as_path())
            .expect("prompt should be written");
        assert!(
            !saved_prompt.contains("STALE_MARKER"),
            "expected stale prompt not to be replayed; prompt was:\n{saved_prompt}"
        );
        assert!(
            saved_prompt.contains("FRESH_DIFF_CONTENT"),
            "expected prompt to include the fresh diff content; prompt was:\n{saved_prompt}"
        );
    }

    // Tests for effective_model_budget_bytes
    #[test]
    fn test_effective_budget_is_min_across_agents() {
        // claude (300KB) + qwen (100KB) = min is 100KB
        let agents = vec!["claude".to_string(), "qwen".to_string()];
        assert_eq!(effective_model_budget_bytes(&agents), GLM_MAX_PROMPT_SIZE);
    }

    #[test]
    fn test_effective_budget_multiple_glm_agents() {
        // All GLM-like agents should return GLM budget
        let agents = vec![
            "qwen".to_string(),
            "deepseek".to_string(),
            "zhipuai".to_string(),
        ];
        assert_eq!(effective_model_budget_bytes(&agents), GLM_MAX_PROMPT_SIZE);
    }

    #[test]
    fn test_effective_budget_claude_only() {
        // Single Claude agent should return Claude budget
        let agents = vec!["claude".to_string()];
        assert_eq!(
            effective_model_budget_bytes(&agents),
            CLAUDE_MAX_PROMPT_SIZE
        );
    }

    #[test]
    fn test_effective_budget_defaults_to_200kb_for_unknown() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let agents = vec!["unknown-agent".to_string()];
        assert_eq!(effective_model_budget_bytes(&agents), MAX_SAFE_PROMPT_SIZE);
    }

    #[test]
    fn test_effective_budget_empty_chain_returns_default() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let agents: Vec<String> = vec![];
        assert_eq!(effective_model_budget_bytes(&agents), MAX_SAFE_PROMPT_SIZE);
    }

    #[test]
    fn test_effective_budget_mixed_agents_uses_smallest() {
        // Mix of Claude (300KB), default (200KB), GLM (100KB) => min is 100KB
        let agents = vec![
            "claude".to_string(),
            "unknown".to_string(),
            "qwen".to_string(),
        ];
        assert_eq!(effective_model_budget_bytes(&agents), GLM_MAX_PROMPT_SIZE);
    }

    // Tests for truncate_diff_to_model_budget determinism
    #[test]
    fn test_truncation_is_deterministic() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let diff = format!("diff --git a/a b/a\n+{}\n", "x".repeat(300_000));
        let budget = 100_000u64;

        let (result1, truncated1) = truncate_diff_to_model_budget(&diff, budget);
        let (result2, truncated2) = truncate_diff_to_model_budget(&diff, budget);

        assert_eq!(result1, result2, "truncation should be deterministic");
        assert_eq!(truncated1, truncated2);
        assert!(truncated1);
    }

    #[test]
    fn test_truncation_within_budget_returns_unchanged() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let diff = "diff --git a/a b/a\n+small change\n";
        let budget = 100_000u64;

        let (result, truncated) = truncate_diff_to_model_budget(diff, budget);

        assert_eq!(result, diff);
        assert!(!truncated);
    }

    #[test]
    fn test_truncation_exactly_at_budget_returns_unchanged() {
        // Create diff exactly at budget size
        let budget = 1000u64;
        let budget_usize = usize::try_from(budget).expect("budget fits in usize");
        let diff_content = "a".repeat(budget_usize);
        let diff = format!("diff --git a/a b/a\n{diff_content}");
        // Ensure we're above budget so truncation occurs
        assert!(diff.len() > budget_usize);

        let (result, truncated) = truncate_diff_to_model_budget(&diff, budget);

        // When above budget, should be truncated
        assert!(truncated);
        assert!(result.len() <= budget_usize + 200); // +200 for truncation message
    }

    #[test]
    fn test_truncation_returns_truncated_flag_when_over_budget() {
        let _cloud = crate::config::types::CloudConfig::disabled();
        let diff = format!("diff --git a/a b/a\n+{}\n", "x".repeat(50_000));
        let budget = 10_000u64;

        let (result, truncated) = truncate_diff_to_model_budget(&diff, budget);

        assert!(truncated, "should indicate truncation occurred");
        assert!(
            result.len() < diff.len(),
            "truncated result should be smaller"
        );
    }

    // Tests for model_budget_bytes_for_agent_name
    #[test]
    fn test_model_budget_for_claude_variants() {
        assert_eq!(
            model_budget_bytes_for_agent_name("claude"),
            CLAUDE_MAX_PROMPT_SIZE
        );
        assert_eq!(
            model_budget_bytes_for_agent_name("Claude"),
            CLAUDE_MAX_PROMPT_SIZE
        );
        assert_eq!(
            model_budget_bytes_for_agent_name("CLAUDE"),
            CLAUDE_MAX_PROMPT_SIZE
        );
        assert_eq!(
            model_budget_bytes_for_agent_name("claude-3"),
            CLAUDE_MAX_PROMPT_SIZE
        );
        assert_eq!(
            model_budget_bytes_for_agent_name("ccs"),
            CLAUDE_MAX_PROMPT_SIZE
        );
        assert_eq!(
            model_budget_bytes_for_agent_name("anthropic"),
            CLAUDE_MAX_PROMPT_SIZE
        );
    }

    #[test]
    fn test_model_budget_for_glm_variants() {
        assert_eq!(
            model_budget_bytes_for_agent_name("glm"),
            GLM_MAX_PROMPT_SIZE
        );
        assert_eq!(
            model_budget_bytes_for_agent_name("zhipuai"),
            GLM_MAX_PROMPT_SIZE
        );
        assert_eq!(
            model_budget_bytes_for_agent_name("qwen"),
            GLM_MAX_PROMPT_SIZE
        );
        assert_eq!(
            model_budget_bytes_for_agent_name("deepseek"),
            GLM_MAX_PROMPT_SIZE
        );
        assert_eq!(
            model_budget_bytes_for_agent_name("zai"),
            GLM_MAX_PROMPT_SIZE
        );
    }

    #[test]
    fn test_model_budget_for_unknown_agents() {
        assert_eq!(
            model_budget_bytes_for_agent_name("unknown"),
            MAX_SAFE_PROMPT_SIZE
        );
        assert_eq!(
            model_budget_bytes_for_agent_name("gpt-4"),
            MAX_SAFE_PROMPT_SIZE
        );
        assert_eq!(
            model_budget_bytes_for_agent_name("custom-agent"),
            MAX_SAFE_PROMPT_SIZE
        );
    }

    #[test]
    fn test_generate_commit_message_with_chain_falls_back_on_failure() {
        // First agent (ccs) fails (exit code 1), second agent (claude) succeeds
        let workspace = MemoryWorkspace::new_test()
            .with_file(
                xml_paths::COMMIT_MESSAGE_XML,
                "<ralph-commit><ralph-subject>feat: fallback worked</ralph-subject></ralph-commit>",
            )
            .with_file(".agent/tmp/commit_message.xsd", "<xsd/>");
        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let mut timer = Timer::new();

        let config = Config::default();
        let registry = AgentRegistry::new().unwrap();
        let template_context = TemplateContext::default();

        // First agent (ccs) fails with exit code 1, second agent (claude) succeeds
        let executor = Arc::new(
            MockProcessExecutor::new()
                .with_agent_result(
                    "ccs",
                    Ok(crate::executor::AgentCommandResult::failure(
                        1,
                        "ccs failed",
                    )),
                )
                .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
        );
        let executor_arc: Arc<dyn ProcessExecutor> = executor.clone();

        let mut runtime = PipelineRuntime {
            timer: &mut timer,
            logger: &logger,
            colors: &colors,
            config: &config,
            executor: executor_arc.as_ref(),
            executor_arc: executor_arc.clone(),
            workspace: &workspace,
            workspace_arc: std::sync::Arc::new(workspace.clone()),
        };

        // Call with a chain of agents - should try ccs, fail, then try claude
        let agents = vec!["ccs".to_string(), "claude".to_string()];
        let result = generate_commit_message_with_chain(
            "diff --git a/a b/a\n+change\n",
            &registry,
            &mut runtime,
            &agents,
            &template_context,
            &workspace,
            &HashMap::new(),
        );

        // Should succeed because claude succeeded after ccs failed
        assert!(
            result.is_ok(),
            "Expected success after fallback, got: {result:?}"
        );
        let result = result.unwrap();
        match result.outcome {
            CommitMessageOutcome::Message(message) => {
                assert_eq!(message, "feat: fallback worked");
            }
            other @ CommitMessageOutcome::Skipped { .. } => {
                panic!("expected message outcome, got: {other:?}")
            }
        }

        // Verify both agents were tried
        let calls = executor.agent_calls();
        assert_eq!(
            calls.len(),
            2,
            "Expected exactly 2 agent calls (ccs failed, claude succeeded), got {}",
            calls.len()
        );
    }

    #[test]
    fn test_run_commit_attempt_treats_skip_as_valid_output_and_logs_successful_extraction() {
        let workspace = MemoryWorkspace::new_test().with_file(
            xml_paths::COMMIT_MESSAGE_XML,
            "<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>",
        );

        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let mut timer = Timer::new();

        let config = Config::default();
        let registry = AgentRegistry::new().unwrap();
        let template_context = TemplateContext::default();

        let executor = Arc::new(
            MockProcessExecutor::new()
                .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
        );
        let executor_arc: Arc<dyn ProcessExecutor> = executor;

        let repo_root = PathBuf::from("/mock/repo");
        let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
        let cloud = crate::config::types::CloudConfig::disabled();
        let mut ctx = PhaseContext {
            config: &config,
            registry: &registry,
            logger: &logger,
            colors: &colors,
            timer: &mut timer,
            developer_agent: "claude",
            reviewer_agent: "claude",
            review_guidelines: None,
            template_context: &template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            prompt_history: HashMap::new(),
            executor: executor_arc.as_ref(),
            executor_arc: executor_arc.clone(),
            repo_root: repo_root.as_path(),
            workspace: &workspace,
            workspace_arc: std::sync::Arc::new(workspace.clone()),
            run_log_context: &run_log_context,
            cloud_reporter: None,
            cloud: &cloud,
        };

        let result = run_commit_attempt(&mut ctx, 1, "diff --git a/a b/a\n+change\n", "claude")
            .expect("run_commit_attempt should succeed");

        assert!(
            result.output_valid,
            "skip should be treated as valid output"
        );
        assert!(
            result.message.is_none(),
            "skip should not produce a message"
        );
        assert_eq!(result.skip_reason.as_deref(), Some("No changes found"));

        // Ensure the extraction attempt is recorded as success (not failure).
        let runs = workspace
            .read_dir(std::path::Path::new(".agent/logs/commit_generation"))
            .expect("read_dir commit_generation");
        let run_dir = runs
            .into_iter()
            .find(|e| {
                e.is_dir()
                    && e.file_name()
                        .is_some_and(|n| n.to_string_lossy().starts_with("run_"))
            })
            .expect("expected a run_* directory");
        let attempt_logs = workspace
            .read_dir(run_dir.path())
            .expect("read_dir run dir");
        let attempt_log = attempt_logs
            .into_iter()
            .find(|e| {
                e.is_file()
                    && e.file_name()
                        .is_some_and(|n| n.to_string_lossy().starts_with("attempt_"))
            })
            .expect("expected an attempt_*.log file");

        let content = workspace
            .read(attempt_log.path())
            .expect("read attempt log");
        assert!(
            content.contains("XML [✓ SUCCESS]"),
            "expected skip extraction to be logged as success; log content was:\n{content}"
        );
    }
}
