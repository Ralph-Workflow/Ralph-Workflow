use super::MainEffectHandler;
use crate::phases::PhaseContext;
use crate::prompts::PromptHistoryEntry;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{ConflictStrategy, PipelineEvent, RebasePhase};
use anyhow::Result;

fn event_for_continue_strategy_remaining_conflicts(
    files: Vec<std::path::PathBuf>,
) -> PipelineEvent {
    if files.is_empty() {
        PipelineEvent::rebase_conflict_resolved(Vec::new())
    } else {
        PipelineEvent::rebase_conflict_detected(files)
    }
}

impl MainEffectHandler {
    pub(super) fn run_rebase(
        &self,
        ctx: &mut PhaseContext<'_>,
        phase: RebasePhase,
        target_branch: &str,
    ) -> Result<EffectResult> {
        if matches!(phase, RebasePhase::Initial) {
            return run_initial_rebase_phase(self, ctx, phase);
        }
        run_non_initial_rebase_phase(ctx, phase, target_branch)
    }

    pub(super) fn resolve_rebase_conflicts(
        ctx: &PhaseContext<'_>,
        strategy: ConflictStrategy,
    ) -> EffectResult {
        use crate::git_helpers::{abort_rebase, continue_rebase, get_conflicted_files};

        match strategy {
            ConflictStrategy::Continue => {
                resolve_conflict_continue(ctx, &continue_rebase, &get_conflicted_files)
            }
            ConflictStrategy::Abort => resolve_conflict_abort(ctx, &abort_rebase),
            ConflictStrategy::Skip => {
                EffectResult::event(PipelineEvent::rebase_conflict_resolved(Vec::new()))
            }
        }
    }
}

fn run_initial_rebase_phase(
    handler: &MainEffectHandler,
    ctx: &mut PhaseContext<'_>,
    phase: RebasePhase,
) -> Result<EffectResult> {
    let run_context = ctx.run_context.clone();
    let mut local_prompt_history = handler.state.prompt_history.clone();
    let run_result = crate::app::rebase::run_initial_rebase(
        ctx.logger,
        *ctx.colors,
        ctx,
        &run_context,
        ctx.executor,
        &mut local_prompt_history,
    )?;

    let event = match run_result.outcome {
        crate::app::rebase::InitialRebaseOutcome::Succeeded { new_head } => {
            PipelineEvent::rebase_succeeded(phase, new_head)
        }
        crate::app::rebase::InitialRebaseOutcome::Skipped { reason } => {
            PipelineEvent::rebase_skipped(phase, reason)
        }
    };

    let result = run_result.prompt_replay_hits.into_iter().fold(
        EffectResult::event(event),
        |r, (key, was_replayed)| {
            r.with_ui_event(crate::reducer::ui_event::UIEvent::PromptReplayHit {
                key,
                was_replayed,
            })
        },
    );
    let result = prompt_captured_events_for_prompt_history_delta(
        &handler.state.prompt_history,
        &local_prompt_history,
    )
    .into_iter()
    .fold(result, |r, ev| r.with_additional_event(ev));

    Ok(result)
}

fn run_non_initial_rebase_phase(
    ctx: &PhaseContext<'_>,
    phase: RebasePhase,
    target_branch: &str,
) -> Result<EffectResult> {
    use crate::git_helpers::rebase_onto;
    match rebase_onto(target_branch, ctx.executor) {
        Ok(_) => Ok(rebase_onto_success_result(ctx, phase)),
        Err(e) => Ok(EffectResult::event(PipelineEvent::rebase_failed(
            phase,
            e.to_string(),
        ))),
    }
}

fn rebase_onto_success_result(ctx: &PhaseContext<'_>, phase: RebasePhase) -> EffectResult {
    use crate::git_helpers::get_conflicted_files;
    let conflicted_files = get_conflicted_files().unwrap_or_default();
    if conflicted_files.is_empty() {
        let new_head = resolve_head_oid(ctx.repo_root, "unknown");
        EffectResult::event(PipelineEvent::rebase_succeeded(phase, new_head))
    } else {
        let files = conflicted_files
            .into_iter()
            .map(std::convert::Into::into)
            .collect();
        EffectResult::event(PipelineEvent::rebase_conflict_detected(files))
    }
}

fn resolve_conflict_continue(
    ctx: &PhaseContext<'_>,
    continue_fn: &dyn Fn(&dyn crate::executor::ProcessExecutor) -> std::io::Result<()>,
    get_conflicts_fn: &dyn Fn() -> std::io::Result<Vec<String>>,
) -> EffectResult {
    match continue_fn(ctx.executor) {
        Ok(()) => {
            let files = get_conflicts_fn()
                .unwrap_or_default()
                .into_iter()
                .map(std::convert::Into::into)
                .collect();
            EffectResult::event(event_for_continue_strategy_remaining_conflicts(files))
        }
        Err(e) => EffectResult::event(PipelineEvent::rebase_failed(
            RebasePhase::PostReview,
            e.to_string(),
        )),
    }
}

fn resolve_conflict_abort(
    ctx: &PhaseContext<'_>,
    abort_fn: &dyn Fn(&dyn crate::executor::ProcessExecutor) -> std::io::Result<()>,
) -> EffectResult {
    match abort_fn(ctx.executor) {
        Ok(()) => {
            let restored_to = resolve_head_oid(ctx.repo_root, "HEAD");
            EffectResult::event(PipelineEvent::rebase_aborted(
                RebasePhase::PostReview,
                restored_to,
            ))
        }
        Err(e) => EffectResult::event(PipelineEvent::rebase_failed(
            RebasePhase::PostReview,
            e.to_string(),
        )),
    }
}

fn resolve_head_oid(repo_root: &std::path::Path, fallback: &str) -> String {
    git2::Repository::open(repo_root).map_or_else(
        |_| fallback.to_string(),
        |repo| {
            repo.head()
                .ok()
                .and_then(|head| head.peel_to_commit().ok())
                .map_or_else(|| fallback.to_string(), |commit| commit.id().to_string())
        },
    )
}

fn prompt_captured_events_for_prompt_history_delta(
    original: &std::collections::HashMap<String, PromptHistoryEntry>,
    updated: &std::collections::HashMap<String, PromptHistoryEntry>,
) -> Vec<PipelineEvent> {
    updated
        .iter()
        .filter_map(|(key, entry)| {
            let should_emit = original.get(key).is_none_or(|existing| {
                existing.content != entry.content || existing.content_id != entry.content_id
            });
            if should_emit {
                Some(PipelineEvent::PromptInput(
                    crate::reducer::event::PromptInputEvent::PromptCaptured {
                        key: key.clone(),
                        content: entry.content.clone(),
                        content_id: entry.content_id.clone(),
                    },
                ))
            } else {
                None
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::prompts::PromptHistoryEntry;

    #[test]
    fn continue_strategy_reports_detected_when_conflicts_remain() {
        use crate::reducer::event::RebaseEvent;
        use std::path::PathBuf;

        let event = event_for_continue_strategy_remaining_conflicts(vec![PathBuf::from("a.txt")]);
        assert!(matches!(
            event,
            PipelineEvent::Rebase(RebaseEvent::ConflictDetected { files })
                if files == vec![PathBuf::from("a.txt")]
        ));
    }

    #[test]
    fn continue_strategy_reports_resolved_when_no_conflicts_remain() {
        use crate::reducer::event::RebaseEvent;
        let event = event_for_continue_strategy_remaining_conflicts(Vec::new());
        assert!(matches!(
            event,
            PipelineEvent::Rebase(RebaseEvent::ConflictResolved { files }) if files.is_empty()
        ));
    }

    #[test]
    fn emits_prompt_captured_when_rebase_updates_existing_prompt_history_entry() {
        let mut original = std::collections::HashMap::new();
        original.insert(
            "planning_conflict_resolution".to_string(),
            PromptHistoryEntry::new("old".to_string(), Some("id1".to_string())),
        );
        let mut updated = original.clone();
        updated.insert(
            "planning_conflict_resolution".to_string(),
            PromptHistoryEntry::new("new".to_string(), Some("id1".to_string())),
        );

        let events = prompt_captured_events_for_prompt_history_delta(&original, &updated);
        assert_eq!(events.len(), 1);
        assert!(matches!(
            &events[0],
            PipelineEvent::PromptInput(crate::reducer::event::PromptInputEvent::PromptCaptured {
                key,
                content,
                content_id: Some(id),
            }) if key == "planning_conflict_resolution" && content == "new" && id == "id1"
        ));
    }

    #[test]
    fn emits_prompt_captured_when_rebase_adds_new_prompt_history_entry() {
        let original = std::collections::HashMap::new();
        let mut updated = std::collections::HashMap::new();
        updated.insert(
            "development_conflict_resolution".to_string(),
            PromptHistoryEntry::from_string("prompt".to_string()),
        );

        let events = prompt_captured_events_for_prompt_history_delta(&original, &updated);
        assert_eq!(events.len(), 1);
        assert!(matches!(
            &events[0],
            PipelineEvent::PromptInput(crate::reducer::event::PromptInputEvent::PromptCaptured {
                key,
                content,
                content_id: None,
            }) if key == "development_conflict_resolution" && content == "prompt"
        ));
    }
}
