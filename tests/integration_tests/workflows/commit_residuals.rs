//! Residual file handling workflow integration tests.
//!
//! Verifies unattended two-pass residual handling:
//! - Pass 1 residuals trigger an automatic second commit pass.
//! - Pass 2 residuals are carried forward to the next cycle (no human interaction).

use crate::common::{
    create_test_config_struct, mock_executor_with_success, run_ralph_cli_with_handlers,
};
use crate::test_timeout::with_default_timeout;
use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
use ralph_workflow::reducer::PipelineState;
use std::path::PathBuf;

/// Standard prompt content for workflow tests.
const STANDARD_PROMPT: &str = r"## Goal

Test unattended residual commit handling

## Acceptance

- Tests pass
";

fn create_handlers_with_residuals(
    pass1_residual: Vec<String>,
    pass2_residual: Vec<String>,
) -> (MockAppEffectHandler, MockEffectHandler) {
    let app_handler = MockAppEffectHandler::new()
        .with_head_oid("a".repeat(40))
        .with_cwd(PathBuf::from("/mock/repo"))
        .with_file("PROMPT.md", STANDARD_PROMPT)
        .with_file("src/foo.rs", "fn foo() {}\n")
        .with_diff("diff --git a/src/foo.rs b/src/foo.rs\n+mock\n")
        .with_staged_changes(true);

    let commit_xml = r"<ralph-commit>
<ralph-subject>feat: selective commit</ralph-subject>
<ralph-files>
  <ralph-file>src/foo.rs</ralph-file>
</ralph-files>
</ralph-commit>";

    let effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0))
        .with_commit_message_xml(commit_xml)
        .with_residual_files_for_pass(1, pass1_residual)
        .with_residual_files_for_pass(2, pass2_residual);

    (app_handler, effect_handler)
}

#[test]
fn residuals_trigger_second_pass_and_pass2_carries_forward() {
    with_default_timeout(|| {
        let pass1 = vec!["src/leftover_1.rs".to_string()];
        let pass2 = vec![
            "src/leftover_2.rs".to_string(),
            "tests/leftover.rs".to_string(),
        ];
        let (mut app_handler, mut effect_handler) =
            create_handlers_with_residuals(pass1, pass2.clone());

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .expect("pipeline should complete");

        // Observable behavior: both residual checks are executed.
        let effects = effect_handler.captured_effects();
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, Effect::CheckResidualFiles { pass: 1 })),
            "expected residual check pass 1"
        );
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, Effect::CheckResidualFiles { pass: 2 })),
            "expected residual check pass 2"
        );

        // Observable behavior: pass-2 residuals are carried forward in state.
        assert_eq!(effect_handler.state.commit_residual_files, pass2);
        assert!(
            !effect_handler.state.commit_is_second_pass,
            "second-pass flag must be cleared after carry-forward"
        );
    });
}

#[test]
fn pass1_residuals_trigger_second_pass_and_pass2_clean_clears_flags() {
    with_default_timeout(|| {
        let pass1 = vec!["src/leftover.rs".to_string()];
        let pass2: Vec<String> = vec![];
        let (mut app_handler, mut effect_handler) = create_handlers_with_residuals(pass1, pass2);

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .expect("pipeline should complete");

        let effects = effect_handler.captured_effects();
        assert!(effects
            .iter()
            .any(|e| matches!(e, Effect::CheckResidualFiles { pass: 1 })));
        assert!(effects
            .iter()
            .any(|e| matches!(e, Effect::CheckResidualFiles { pass: 2 })));

        assert!(effect_handler.state.commit_residual_files.is_empty());
        assert!(!effect_handler.state.commit_is_second_pass);
    });
}
