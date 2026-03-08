use super::MainEffectHandler;
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{ConflictStrategy, PipelineEvent, RebasePhase};
use anyhow::Result;

impl MainEffectHandler {
    pub(super) fn run_rebase(
        &self,
        ctx: &mut PhaseContext<'_>,
        phase: RebasePhase,
        target_branch: &str,
    ) -> Result<EffectResult> {
        use crate::git_helpers::{get_conflicted_files, rebase_onto};

        if matches!(phase, RebasePhase::Initial) {
            let run_context = ctx.run_context.clone();
            // Start with the current reducer-owned prompt history so rebase conflict
            // resolution can replay stored prompts and new ones are emitted as events.
            let mut local_prompt_history = self.state.prompt_history.clone();
            let outcome = crate::app::rebase::run_initial_rebase(
                ctx,
                &run_context,
                ctx.executor,
                &mut local_prompt_history,
            )?;

            let event = match outcome {
                crate::app::rebase::InitialRebaseOutcome::Succeeded { new_head } => {
                    PipelineEvent::rebase_succeeded(phase, new_head)
                }
                crate::app::rebase::InitialRebaseOutcome::Skipped { reason } => {
                    PipelineEvent::rebase_skipped(phase, reason)
                }
            };

            // Emit PromptCaptured events for any prompts newly captured during rebase
            // conflict resolution, so the reducer-owned PipelineState.prompt_history
            // stays consistent with what was saved to disk in the interim checkpoints.
            let mut result = EffectResult::event(event);
            for (key, entry) in &local_prompt_history {
                if !self.state.prompt_history.contains_key(key) {
                    result = result.with_additional_event(PipelineEvent::PromptInput(
                        crate::reducer::event::PromptInputEvent::PromptCaptured {
                            key: key.clone(),
                            content: entry.content.clone(),
                            content_id: entry.content_id.clone(),
                        },
                    ));
                }
            }

            return Ok(result);
        }

        match rebase_onto(target_branch, ctx.executor) {
            Ok(_) => {
                let conflicted_files = get_conflicted_files().unwrap_or_default();

                if conflicted_files.is_empty() {
                    let new_head = git2::Repository::open(ctx.repo_root).map_or_else(
                        |_| "unknown".to_string(),
                        |repo| {
                            repo.head()
                                .ok()
                                .and_then(|head| head.peel_to_commit().ok())
                                .map_or_else(
                                    || "unknown".to_string(),
                                    |commit| commit.id().to_string(),
                                )
                        },
                    );

                    Ok(EffectResult::event(PipelineEvent::rebase_succeeded(
                        phase, new_head,
                    )))
                } else {
                    let files = conflicted_files
                        .into_iter()
                        .map(std::convert::Into::into)
                        .collect();
                    Ok(EffectResult::event(
                        PipelineEvent::rebase_conflict_detected(files),
                    ))
                }
            }
            Err(e) => Ok(EffectResult::event(PipelineEvent::rebase_failed(
                phase,
                e.to_string(),
            ))),
        }
    }

    pub(super) fn resolve_rebase_conflicts(
        ctx: &PhaseContext<'_>,
        strategy: ConflictStrategy,
    ) -> EffectResult {
        use crate::git_helpers::{abort_rebase, continue_rebase, get_conflicted_files};

        match strategy {
            ConflictStrategy::Continue => match continue_rebase(ctx.executor) {
                Ok(()) => {
                    let files = get_conflicted_files()
                        .unwrap_or_default()
                        .into_iter()
                        .map(std::convert::Into::into)
                        .collect();

                    EffectResult::event(PipelineEvent::rebase_conflict_resolved(files))
                }
                Err(e) => EffectResult::event(PipelineEvent::rebase_failed(
                    RebasePhase::PostReview,
                    e.to_string(),
                )),
            },
            ConflictStrategy::Abort => match abort_rebase(ctx.executor) {
                Ok(()) => {
                    let restored_to = git2::Repository::open(ctx.repo_root).map_or_else(
                        |_| "HEAD".to_string(),
                        |repo| {
                            repo.head()
                                .ok()
                                .and_then(|head| head.peel_to_commit().ok())
                                .map_or_else(
                                    || "HEAD".to_string(),
                                    |commit| commit.id().to_string(),
                                )
                        },
                    );

                    EffectResult::event(PipelineEvent::rebase_aborted(
                        RebasePhase::PostReview,
                        restored_to,
                    ))
                }
                Err(e) => EffectResult::event(PipelineEvent::rebase_failed(
                    RebasePhase::PostReview,
                    e.to_string(),
                )),
            },
            ConflictStrategy::Skip => {
                EffectResult::event(PipelineEvent::rebase_conflict_resolved(Vec::new()))
            }
        }
    }
}
