impl MainEffectHandler {
    pub(super) fn prepare_fix_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        use crate::agents::AgentRole;
        use crate::prompts::{
            get_stored_or_generate_prompt, prompt_fix_xml_with_context,
            prompt_fix_xsd_retry_with_context, PromptScopeKey, RetryMode,
        };
        use crate::reducer::prompt_inputs::sha256_hex_str;
        use std::path::Path;

        let tmp_dir = Path::new(".agent/tmp");
        if !ctx.workspace.exists(tmp_dir) {
            ctx.workspace.create_dir_all(tmp_dir).map_err(|err| {
                ErrorEvent::WorkspaceCreateDirAllFailed {
                    path: tmp_dir.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
            })?;
        }

        let prompt_content = match ctx.workspace.read(Path::new(".agent/PROMPT.md.backup")) {
            Ok(s) => s,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                ctx.logger.warn(
                    "Missing .agent/PROMPT.md.backup; embedding sentinel in fix prompt input",
                );
                "[MISSING INPUT: .agent/PROMPT.md.backup]\n\nNo PROMPT backup was found. Continuing without original request context.\n".to_string()
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: ".agent/PROMPT.md.backup".to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };
        // Use sentinel PLAN content when missing (consistent with review phase)
        let plan_content = match ctx.workspace.read(Path::new(".agent/PLAN.md")) {
            Ok(s) => s,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                Self::sentinel_plan_content(ctx.config.isolation_mode)
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: ".agent/PLAN.md".to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };
        let issues_content = match ctx.workspace.read(Path::new(".agent/ISSUES.md")) {
            Ok(s) => s,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                ctx.logger
                    .warn("Missing .agent/ISSUES.md; embedding sentinel in fix prompt input");
                "[MISSING INPUT: .agent/ISSUES.md]\n\nNo ISSUES.md was found. This may indicate a cleaned workspace or a skipped review pass.\n".to_string()
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: ".agent/ISSUES.md".to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };

        let continuation_state = &self.state.continuation;
        let is_xsd_retry = matches!(prompt_mode, PromptMode::XsdRetry);
        let last_output = if is_xsd_retry {
            match ctx.workspace.read(Path::new(xml_paths::FIX_RESULT_XML)) {
                Ok(s) => s,
                Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                    // Try reading from the archived .processed file as a fallback
                    let processed_path = Path::new(".agent/tmp/fix_result.xml.processed");
                    ctx.workspace.read(processed_path).map_or_else(
                        |_| String::new(),
                        |output| {
                            ctx.logger
                                .info("XSD retry: using archived .processed file as last output");
                            output
                        },
                    )
                }
                Err(err) => {
                    return Err(ErrorEvent::WorkspaceReadFailed {
                        path: xml_paths::FIX_RESULT_XML.to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    }
                    .into());
                }
            }
        } else {
            String::new()
        };
        let mut xsd_error_for_validation: Option<String> = None;
        let (
            prompt_key,
            fix_prompt,
            was_replayed,
            template_name,
            prompt_content_id,
            should_validate,
        ) = match prompt_mode {
            PromptMode::XsdRetry => {
                let scope_key = PromptScopeKey::for_fix(
                    pass,
                    RetryMode::Xsd {
                        count: continuation_state.invalid_output_attempts,
                    },
                    self.state.recovery_epoch,
                );
                let prompt_key = scope_key.to_string();
                // Use the actual XSD error from state, or fall back to generic message
                let xsd_error = continuation_state
                    .last_fix_xsd_error
                    .as_deref()
                    .filter(|s| !s.trim().is_empty())
                    .unwrap_or("XML output failed validation. Provide valid XML output.");
                xsd_error_for_validation = Some(xsd_error.to_string());

                let prompt_id = sha256_hex_str(&prompt_content);
                let plan_id = sha256_hex_str(&plan_content);
                let issues_id = sha256_hex_str(&issues_content);
                let last_output_id = sha256_hex_str(&last_output);
                let current_prompt_content_id = sha256_hex_str(&format!(
                    "fix_xsd_retry|{prompt_id}|{plan_id}|{issues_id}|{xsd_error}|{last_output_id}"
                ));

                let (prompt, was_replayed) = get_stored_or_generate_prompt(
                    &scope_key,
                    &self.state.prompt_history,
                    Some(&current_prompt_content_id),
                    || {
                        prompt_fix_xsd_retry_with_context(
                            ctx.template_context,
                            &issues_content,
                            xsd_error,
                            &last_output,
                            ctx.workspace,
                        )
                    },
                );
                (
                    prompt_key,
                    prompt,
                    was_replayed,
                    "fix_mode_xsd_retry",
                    Some(current_prompt_content_id),
                    true,
                )
            }
            PromptMode::SameAgentRetry => {
                // Same-agent retry: prepend retry guidance to the last prepared prompt for this
                // phase (preserves XSD retry / continuation context if present).
                let retry_preamble =
                    crate::reducer::handler::retry_guidance::same_agent_retry_preamble(
                        continuation_state,
                    );
                let scope_key = PromptScopeKey::for_fix(
                    pass,
                    RetryMode::SameAgent {
                        count: continuation_state.same_agent_retry_count,
                    },
                    self.state.recovery_epoch,
                );
                let prompt_key = scope_key.to_string();

                let prompt_id = sha256_hex_str(&prompt_content);
                let plan_id = sha256_hex_str(&plan_content);
                let issues_id = sha256_hex_str(&issues_content);
                let current_prompt_content_id = sha256_hex_str(&format!(
                    "fix_same_agent_retry|count:{}|{prompt_id}|{plan_id}|{issues_id}",
                    continuation_state.same_agent_retry_count
                ));

                let mut should_validate = false;
                let (prompt, was_replayed) = get_stored_or_generate_prompt(
                    &scope_key,
                    &self.state.prompt_history,
                    Some(&current_prompt_content_id),
                    || {
                        let (base_prompt, local_should_validate) = ctx
                                .workspace
                                .read(Path::new(".agent/tmp/fix_prompt.txt"))
                                .map_or_else(
                                    |_| {
                                        (
                                            prompt_fix_xml_with_context(
                                                ctx.template_context,
                                                &prompt_content,
                                                &plan_content,
                                                &issues_content,
                                                &[],
                                                ctx.workspace,
                                            ),
                                            true,
                                        )
                                    },
                                    |previous_prompt| {
                                        let previous_base = crate::reducer::handler::retry_guidance::strip_existing_same_agent_retry_preamble(&previous_prompt)
                                            .to_string();
                                        let freshly_rendered_base = prompt_fix_xml_with_context(
                                            ctx.template_context,
                                            &prompt_content,
                                            &plan_content,
                                            &issues_content,
                                            &[],
                                            ctx.workspace,
                                        );
                                        if previous_base == freshly_rendered_base {
                                            (previous_base, false)
                                        } else {
                                            (freshly_rendered_base, true)
                                        }
                                    },
                                );
                        should_validate = local_should_validate;
                        format!("{retry_preamble}\n{base_prompt}")
                    },
                );
                (
                    prompt_key,
                    prompt,
                    was_replayed,
                    "fix_mode_xml",
                    Some(current_prompt_content_id),
                    should_validate,
                )
            }
            PromptMode::Normal => {
                let scope_key =
                    PromptScopeKey::for_fix(pass, RetryMode::Normal, self.state.recovery_epoch);
                let prompt_key = scope_key.to_string();

                let prompt_id = sha256_hex_str(&prompt_content);
                let plan_id = sha256_hex_str(&plan_content);
                let issues_id = sha256_hex_str(&issues_content);
                let current_prompt_content_id =
                    sha256_hex_str(&format!("fix_xml|{prompt_id}|{plan_id}|{issues_id}"));

                let (prompt, was_replayed) = get_stored_or_generate_prompt(
                    &scope_key,
                    &self.state.prompt_history,
                    Some(&current_prompt_content_id),
                    || {
                        // Use log-based rendering
                        let rendered = crate::prompts::review::prompt_fix_xml_with_log(
                            ctx.template_context,
                            &prompt_content,
                            &plan_content,
                            &issues_content,
                            &[],
                            ctx.workspace,
                            "fix_mode_xml",
                        );
                        rendered.content
                    },
                );
                (
                    prompt_key,
                    prompt,
                    was_replayed,
                    "fix_mode_xml",
                    Some(current_prompt_content_id),
                    true,
                )
            }
            PromptMode::Continuation => {
                return Err(ErrorEvent::FixContinuationNotSupported.into());
            }
        };
        let rendered_log = if should_validate && !was_replayed {
            // Re-generate to get the log for validation
            // Only validate freshly generated prompts, not replayed ones
            let rendered = if matches!(prompt_mode, PromptMode::XsdRetry) {
                let xsd_error = xsd_error_for_validation
                    .as_deref()
                    .unwrap_or("XML output failed validation. Provide valid XML output.");
                crate::prompts::review::prompt_fix_xsd_retry_with_log(
                    ctx.template_context,
                    xsd_error,
                    &last_output,
                    ctx.workspace,
                    template_name,
                )
            } else {
                crate::prompts::review::prompt_fix_xml_with_log(
                    ctx.template_context,
                    &prompt_content,
                    &plan_content,
                    &issues_content,
                    &[],
                    ctx.workspace,
                    template_name,
                )
            };

            if !rendered.log.is_complete() {
                let missing = rendered.log.unsubstituted.clone();
                let result = EffectResult::event(PipelineEvent::template_rendered(
                    crate::reducer::event::PipelinePhase::Review,
                    template_name.to_string(),
                    rendered.log,
                ))
                .with_ui_event(UIEvent::PromptReplayHit {
                    key: prompt_key,
                    was_replayed,
                })
                .with_additional_event(
                    PipelineEvent::agent_template_variables_invalid(
                        AgentRole::Reviewer,
                        template_name.to_string(),
                        missing,
                        Vec::new(),
                    ),
                );
                return Ok(result);
            }
            Some(rendered.log)
        } else {
            None
        };

        // Prepare PromptCaptured event if this is a freshly generated prompt
        let prompt_captured_event = if was_replayed {
            None
        } else {
            Some(crate::reducer::event::PipelineEvent::PromptInput(
                crate::reducer::event::PromptInputEvent::PromptCaptured {
                    key: prompt_key.clone(),
                    content: fix_prompt.clone(),
                    content_id: prompt_content_id,
                },
            ))
        };

        // Write prompt file (non-fatal: if write fails, log warning and continue)
        if let Err(err) = ctx
            .workspace
            .write(Path::new(".agent/tmp/fix_prompt.txt"), &fix_prompt)
        {
            ctx.logger.warn(&format!(
                "Failed to write fix prompt file: {err}. Pipeline will continue (loop recovery will handle convergence)."
            ));
        }

        let mut result = EffectResult::event(PipelineEvent::fix_prompt_prepared(pass))
            .with_ui_event(UIEvent::PromptReplayHit {
                key: prompt_key,
                was_replayed,
            });

        // Emit PromptCaptured event to update reducer-owned prompt history (RFC-007)
        if let Some(event) = prompt_captured_event {
            result = result.with_additional_event(event);
        }

        if let Some(log) = rendered_log {
            result = result.with_additional_event(PipelineEvent::template_rendered(
                crate::reducer::event::PipelinePhase::Review,
                template_name.to_string(),
                log,
            ));
        }

        Ok(result)
    }

    pub(super) fn invoke_fix_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        use crate::agents::AgentRole;
        use std::path::Path;

        // Normalize agent chain state before invocation for determinism
        self.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Fix);

        let prompt = match ctx.workspace.read(Path::new(".agent/tmp/fix_prompt.txt")) {
            Ok(s) => s,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                return Err(ErrorEvent::FixPromptMissing.into());
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: ".agent/tmp/fix_prompt.txt".to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };

        let agent = self
            .state
            .agent_chain
            .current_agent()
            .cloned()
            .unwrap_or_else(|| ctx.reviewer_agent.to_string());

        let mut result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Fix,
            AgentRole::Reviewer,
            &agent,
            None,
            prompt,
        )?;
        if result.additional_events.iter().any(|e| {
            matches!(
                e,
                PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
            )
        }) {
            result = result.with_additional_event(PipelineEvent::fix_agent_invoked(pass));
        }
        Ok(result)
    }

    pub(super) fn extract_fix_result_xml(&self, ctx: &PhaseContext<'_>, pass: u32) -> EffectResult {
        use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
        use std::path::Path;

        let fix_xml = Path::new(xml_paths::FIX_RESULT_XML);
        match ctx.workspace.read(fix_xml) {
            Ok(_) => EffectResult::event(PipelineEvent::fix_result_xml_extracted(pass)),
            Err(err) => {
                let detail = if err.kind() == std::io::ErrorKind::NotFound {
                    None
                } else {
                    Some(format!(
                        "{:?}: {}",
                        WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                        err
                    ))
                };
                EffectResult::event(PipelineEvent::fix_result_xml_missing(
                    pass,
                    self.state.continuation.invalid_output_attempts,
                    detail,
                ))
            }
        }
    }

    pub(super) fn validate_fix_result_xml(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> EffectResult {
        use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
        use crate::files::llm_output_extraction::validate_fix_result_xml;
        use std::path::Path;

        let fix_xml = match ctx.workspace.read(Path::new(xml_paths::FIX_RESULT_XML)) {
            Ok(s) => s,
            Err(err) => {
                let detail = if err.kind() == std::io::ErrorKind::NotFound {
                    None
                } else {
                    Some(format!(
                        "{:?}: {}",
                        WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                        err
                    ))
                };
                return EffectResult::event(PipelineEvent::fix_output_validation_failed(
                    pass,
                    self.state.continuation.invalid_output_attempts,
                    detail,
                ));
            }
        };

        match validate_fix_result_xml(&fix_xml) {
            Ok(elements) => {
                let status = crate::reducer::state::FixStatus::parse(&elements.status)
                    .unwrap_or(crate::reducer::state::FixStatus::Failed);
                EffectResult::with_ui(
                    PipelineEvent::fix_result_xml_validated(pass, status, elements.summary),
                    vec![UIEvent::XmlOutput {
                        xml_type: XmlOutputType::FixResult,
                        content: fix_xml,
                        context: Some(XmlOutputContext {
                            iteration: None,
                            pass: Some(pass),
                            snippets: Vec::new(),
                        }),
                    }],
                )
            }
            Err(err) => EffectResult::event(PipelineEvent::fix_output_validation_failed(
                pass,
                self.state.continuation.invalid_output_attempts,
                Some(err.format_for_ai_retry()),
            )),
        }
    }

    pub(super) fn apply_fix_outcome(
        &self,
        _ctx: &mut PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        self.state
            .fix_validated_outcome
            .as_ref()
            .filter(|o| o.pass == pass)
            .ok_or(ErrorEvent::ValidatedFixOutcomeMissing { pass })?;

        Ok(EffectResult::event(PipelineEvent::fix_outcome_applied(
            pass,
        )))
    }

    pub(super) fn archive_fix_result_xml(ctx: &PhaseContext<'_>, pass: u32) -> EffectResult {
        use crate::files::llm_output_extraction::archive_xml_file_with_workspace;
        use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
        use std::path::Path;

        archive_xml_file_with_workspace(ctx.workspace, Path::new(xml_paths::FIX_RESULT_XML));
        EffectResult::event(PipelineEvent::fix_result_xml_archived(pass))
    }
}
