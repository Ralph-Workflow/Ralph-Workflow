// XSD retry last-output materialization for the review phase.
//
// Extracts the block that reads the last reviewer output, checks if it has already
// been materialized, and emits the relevant pipeline events when it has not.

impl MainEffectHandler {
    /// Materialize the last reviewer output for an XSD retry attempt.
    ///
    /// Reads the last output from `.agent/tmp/issues.xml` (or the archived
    /// `.processed` fallback), checks whether the content has already been
    /// materialized for this pass, and — when it has not — writes
    /// `.agent/tmp/last_output.xml` and returns the events that describe the
    /// materialization (and an optional oversize warning).
    ///
    /// # Returns
    ///
    /// A `Vec` of zero or more `PipelineEvent`s to be added to `additional_events`
    /// in the caller.
    pub(in crate::reducer::handler) fn materialize_xsd_retry_last_output(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> Result<Vec<PipelineEvent>> {
        let last_output = match ctx.workspace.read(Path::new(xml_paths::ISSUES_XML)) {
            Ok(output) => output,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                // The canonical file was archived after successful validation or a previous retry.
                // Try reading from the archived .processed file as a fallback.
                let processed_path = Path::new(".agent/tmp/issues.xml.processed");
                ctx.workspace.read(processed_path).map_or_else(
                    |_| {
                        ctx.logger.warn(
                            "Missing .agent/tmp/issues.xml and .processed fallback; using empty output for review XSD retry",
                        );
                        String::new()
                    },
                    |output| {
                        ctx.logger
                            .info("XSD retry: using archived .processed file as last output");
                        output
                    },
                )
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: xml_paths::ISSUES_XML.to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };

        let content_id_sha256 = sha256_hex_str(&last_output);
        let consumer_signature_sha256 = self.state.agent_chain.consumer_signature_sha256();
        let inline_budget_bytes = MAX_INLINE_CONTENT_SIZE as u64;
        let last_output_bytes = last_output.len() as u64;

        let already_materialized = self
            .state
            .prompt_inputs
            .xsd_retry_last_output
            .as_ref()
            .is_some_and(|m| {
                m.phase == crate::reducer::event::PipelinePhase::Review
                    && m.scope_id == pass
                    && m.last_output.content_id_sha256 == content_id_sha256
                    && m.last_output.consumer_signature_sha256 == consumer_signature_sha256
            });

        if already_materialized {
            return Ok(Vec::new());
        }

        let last_output_path = Path::new(".agent/tmp/last_output.xml");
        ctx.workspace
            .write_atomic(last_output_path, &last_output)
            .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
                path: last_output_path.display().to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        let input = MaterializedPromptInput {
            kind: PromptInputKind::LastOutput,
            content_id_sha256: content_id_sha256.clone(),
            consumer_signature_sha256,
            original_bytes: last_output_bytes,
            final_bytes: last_output_bytes,
            model_budget_bytes: None,
            inline_budget_bytes: Some(inline_budget_bytes),
            representation: PromptInputRepresentation::FileReference {
                path: last_output_path.to_path_buf(),
            },
            reason: PromptMaterializationReason::PolicyForcedReference,
        };

        let mut events = vec![PipelineEvent::xsd_retry_last_output_materialized(
            crate::reducer::event::PipelinePhase::Review,
            pass,
            input,
        )];

        if last_output_bytes > inline_budget_bytes {
            events.push(PipelineEvent::prompt_input_oversize_detected(
                crate::reducer::event::PipelinePhase::Review,
                PromptInputKind::LastOutput,
                content_id_sha256,
                last_output_bytes,
                inline_budget_bytes,
                "xsd-retry-context".to_string(),
            ));
        }

        Ok(events)
    }
}
