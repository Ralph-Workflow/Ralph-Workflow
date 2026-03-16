// Step lifecycle formatting.

struct StepFinishRenderContext<'a> {
    is_duplicate: bool,
    was_streaming: bool,
    metrics: &'a crate::json_parser::health::StreamingQualityMetrics,
    text_flush_non_tty: &'a str,
    terminal_mode: TerminalMode,
    prefix: &'a str,
    colors: crate::logger::Colors,
}

impl OpenCodeParser {
    fn derive_step_id(&self, event: &OpenCodeEvent, session: &str) -> String {
        let step_id = event.part.as_ref().and_then(|part| {
            part.message_id.clone().or_else(|| {
                part.id
                    .as_ref()
                    .map(|id| format!("{session}:{id}"))
                    .or_else(|| {
                        part.snapshot
                            .as_ref()
                            .map(|snapshot| format!("{session}:{snapshot}"))
                    })
            })
        });

        step_id.unwrap_or_else(|| self.next_fallback_step_id(session, event.timestamp))
    }

    fn ensure_current_step_id_for_finish(&self, event: &OpenCodeEvent) {
        if self
            .streaming_session
            .borrow()
            .get_current_message_id()
            .is_some()
        {
            return;
        }

        let session = event.session_id.as_deref().unwrap_or("unknown");
        let step_id = self.derive_step_id(event, session);
        self.streaming_session
            .borrow_mut()
            .set_current_message_id(Some(step_id));
    }

    fn flush_non_tty_accumulated_text(
        &self,
        terminal_mode: TerminalMode,
        prefix: &str,
        colors: crate::logger::Colors,
    ) -> String {
        match terminal_mode {
            TerminalMode::Full => String::new(),
            TerminalMode::Basic | TerminalMode::None => {
                let session = self.streaming_session.borrow();
                let mut out = String::new();

                for key in session.accumulated_keys(ContentType::Text) {
                    let accumulated = session
                        .get_accumulated(ContentType::Text, &key)
                        .unwrap_or("");
                    let sanitized =
                        crate::json_parser::delta_display::sanitize_for_display(accumulated);
                    if sanitized.is_empty() {
                        continue;
                    }

                    match terminal_mode {
                        TerminalMode::Basic => {
                            let _ = writeln!(
                                out,
                                "{}[{}]{} {}{}{}",
                                colors.dim(),
                                prefix,
                                colors.reset(),
                                colors.white(),
                                sanitized,
                                colors.reset()
                            );
                        }
                        TerminalMode::None => {
                            let _ = writeln!(out, "[{prefix}] {sanitized}");
                        }
                        TerminalMode::Full => unreachable!(),
                    }
                }

                out
            }
        }
    }

    fn format_tokens_summary(tokens: &OpenCodeTokens) -> String {
        let input = tokens.input.unwrap_or(0);
        let output = tokens.output.unwrap_or(0);
        let reasoning = tokens.reasoning.unwrap_or(0);
        let cache_read = tokens
            .cache
            .as_ref()
            .and_then(|cache| cache.read)
            .unwrap_or(0);

        if reasoning > 0 {
            format!("in:{input} out:{output} reason:{reasoning} cache:{cache_read}")
        } else if cache_read > 0 {
            format!("in:{input} out:{output} cache:{cache_read}")
        } else {
            format!("in:{input} out:{output}")
        }
    }

    fn format_step_finish_payload(
        &self,
        part: &OpenCodePart,
        context: &StepFinishRenderContext<'_>,
    ) -> String {
        let reason = part.reason.as_deref().unwrap_or("unknown");
        let cost = part.cost.unwrap_or(0.0);
        let tokens_str = part
            .tokens
            .as_ref()
            .map_or_else(String::new, Self::format_tokens_summary);

        let is_success = reason == "tool-calls" || reason == "end_turn";
        let icon = if is_success { CHECK } else { CROSS };
        let color = if is_success {
            context.colors.green()
        } else {
            context.colors.yellow()
        };

        let newline_prefix = if context.is_duplicate || context.was_streaming {
            let completion = TextDeltaRenderer::render_completion(context.terminal_mode);
            let show_metrics = (self.verbosity.is_debug() || self.show_streaming_metrics)
                && context.metrics.total_deltas > 0;
            if show_metrics {
                format!("{}\n{}", completion, context.metrics.format(context.colors))
            } else {
                completion
            }
        } else {
            String::new()
        };

        let mut out = format!(
            "{}{}{}[{}]{} {}{} Step finished{} {}({}",
            context.text_flush_non_tty,
            newline_prefix,
            context.colors.dim(),
            context.prefix,
            context.colors.reset(),
            color,
            icon,
            context.colors.reset(),
            context.colors.dim(),
            reason
        );
        if !tokens_str.is_empty() {
            let _ = write!(out, ", {tokens_str}");
        }
        if cost > 0.0 {
            let _ = write!(out, ", ${cost:.4}");
        }
        let _ = writeln!(out, "){}", context.colors.reset());
        out
    }

    /// Format a `step_start` event
    pub(super) fn format_step_start_event(&self, event: &OpenCodeEvent) -> String {
        let colors = self.colors;
        let prefix = &self.display_name;
        let session = event.session_id.as_deref().unwrap_or("unknown");
        let step_id = self.derive_step_id(event, session);

        // Defensive: OpenCode can emit duplicate `step_start` events for the same message.
        if self
            .streaming_session
            .borrow()
            .get_current_message_id()
            .is_some_and(|current| current == step_id)
        {
            return String::new();
        }

        self.streaming_session.borrow_mut().on_message_start();
        self.last_rendered_content.borrow_mut().clear();
        self.streaming_session
            .borrow_mut()
            .set_current_message_id(Some(step_id));

        let snapshot = event
            .part
            .as_ref()
            .and_then(|p| p.snapshot.as_ref())
            .map(|s| format!("({s:.8}...)"))
            .unwrap_or_default();
        format!(
            "{}[{}]{} {}Step started{} {}{}{}\n",
            colors.dim(),
            prefix,
            colors.reset(),
            colors.cyan(),
            colors.reset(),
            colors.dim(),
            snapshot,
            colors.reset()
        )
    }

    /// Format a `step_finish` event
    pub(super) fn format_step_finish_event(&self, event: &OpenCodeEvent) -> String {
        let colors = self.colors;
        let prefix = &self.display_name;

        self.ensure_current_step_id_for_finish(event);

        let session = self.streaming_session.borrow();
        let is_duplicate = session.get_current_message_id().map_or_else(
            || session.has_any_streamed_content(),
            |message_id| session.is_duplicate_final_message(message_id),
        );
        let was_streaming = session.has_any_streamed_content();
        let metrics = session.get_streaming_quality_metrics();
        drop(session);

        let _was_in_block = self.streaming_session.borrow_mut().on_message_stop();

        let terminal_mode = *self.terminal_mode.borrow();
        let text_flush_non_tty = self.flush_non_tty_accumulated_text(terminal_mode, prefix, colors);
        let render_context = StepFinishRenderContext {
            is_duplicate,
            was_streaming,
            metrics: &metrics,
            text_flush_non_tty: &text_flush_non_tty,
            terminal_mode,
            prefix,
            colors,
        };

        event.part.as_ref().map_or_else(String::new, |part| {
            self.format_step_finish_payload(part, &render_context)
        })
    }
}
