fn is_gemini_control_event(event: &GeminiEvent) -> bool {
    matches!(event, GeminiEvent::Init { .. } | GeminiEvent::Result { .. })
}

fn record_unparsed_event(trimmed: &str, line: &str, monitor: &HealthMonitor) {
    if !trimmed.starts_with('{') { monitor.record_ignored(); return; }
    match serde_json::from_str::<GeminiEvent>(line) {
        Ok(event) if is_gemini_control_event(&event) => monitor.record_control_event(),
        Ok(_) => monitor.record_unknown_event(),
        Err(_) => monitor.record_parse_error(),
    }
}

impl GeminiParser {
    fn debug_print_line(&mut self, line: &str, c: Colors) {
        self.with_printer_mut(|printer| {
            if writeln!(printer, "{}[DEBUG]{} {}{}{}", c.dim(), c.reset(), c.dim(), line, c.reset()).is_ok() {
                printer.flush().ok();
            }
        });
    }

    fn dispatch_parsed_or_unparsed(&mut self, line: &str, trimmed: &str, monitor: &HealthMonitor) {
        match self.parse_event(line) {
            Some(output) => {
                monitor.record_parsed();
                self.with_printer_mut(|printer| {
                    if write!(printer, "{output}").is_ok() { printer.flush().ok(); }
                });
            }
            None => record_unparsed_event(trimmed, line, monitor),
        }
    }

    fn process_batch_line(
        &mut self,
        line: &str,
        c: Colors,
        monitor: &HealthMonitor,
        log_buffer: &mut Vec<u8>,
        logging_enabled: bool,
    ) {
        let trimmed = line.trim();
        if trimmed.is_empty() { return; }
        if self.verbosity.is_debug() { self.debug_print_line(line, c); }
        self.dispatch_parsed_or_unparsed(line, trimmed, monitor);
        if logging_enabled { writeln!(log_buffer, "{line}").ok(); }
    }

    fn run_stream_loop<R: BufRead>(
        &mut self,
        reader: &mut R,
        incremental_parser: &mut crate::json_parser::incremental_parser::IncrementalNdjsonParser,
        c: Colors,
        monitor: &HealthMonitor,
        log_buffer: &mut Vec<u8>,
        logging_enabled: bool,
    ) -> std::io::Result<()> {
        loop {
            let chunk = reader.fill_buf()?;
            if chunk.is_empty() { break; }
            let consumed = chunk.len();
            let taken = std::mem::take(incremental_parser);
            let (new_parser, batch) = taken.feed_and_get_events(chunk);
            *incremental_parser = new_parser;
            reader.consume(consumed);
            batch.into_iter().for_each(|line| {
                self.process_batch_line(&line, c, monitor, log_buffer, logging_enabled);
            });
        }
        Ok(())
    }

    fn finalize_stream(
        &mut self,
        workspace: &dyn crate::workspace::Workspace,
        monitor: &HealthMonitor,
        c: Colors,
        log_buffer: &[u8],
    ) -> std::io::Result<()> {
        if let Some(log_path) = &self.log_path {
            workspace.append_bytes(log_path, log_buffer)?;
        }
        if let Some(warning) = monitor.check_and_warn(c) {
            self.with_printer_mut(|printer| { writeln!(printer, "{warning}\n").ok(); });
        }
        Ok(())
    }

    /// Parse a stream of Gemini NDJSON events
    pub(crate) fn parse_stream<R: BufRead>(
        &mut self,
        mut reader: R,
        workspace: &dyn crate::workspace::Workspace,
    ) -> std::io::Result<()> {
        use crate::json_parser::incremental_parser::IncrementalNdjsonParser;
        let c = self.colors;
        let monitor = HealthMonitor::new("Gemini");
        let logging_enabled = self.log_path.is_some();
        let mut log_buffer: Vec<u8> = Vec::new();
        let mut incremental_parser = IncrementalNdjsonParser::new();
        self.run_stream_loop(&mut reader, &mut incremental_parser, c, &monitor, &mut log_buffer, logging_enabled)?;
        self.finalize_stream(workspace, &monitor, c, &log_buffer)
    }
}
