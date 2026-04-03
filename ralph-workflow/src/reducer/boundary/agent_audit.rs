// Session and audit trail helpers for agent invocation.
// Split from agent.rs to keep file size < 1000 lines.
// This file is included (not mod'd) from agent.rs.

fn create_and_store_agent_session(
    ctx: &mut PhaseContext<'_>,
    drain: AgentDrain,
    attempt: u32,
    state: &crate::reducer::state::PipelineState,
) -> AgentSession {
    let run_id = state
        .cloud
        .run_id
        .clone()
        .unwrap_or_else(|| "unknown".to_string());
    let session_drain = SessionDrain::from(drain);
    let session =
        AgentSession::for_drain_with_created_at(run_id, session_drain, attempt, SystemTime::now());
    log_session_handshake(ctx, &session);
    ctx.active_session = Some(session.clone());
    session
}

fn log_session_handshake(ctx: &PhaseContext<'_>, session: &AgentSession) {
    let handshake = SessionHandshake::from_session(session);
    ctx.logger.info(&format!(
        "RFC-009 session handshake: session_id={}, drain={}, protocol={}, capabilities={:?}, policy_flags={:?}",
        handshake.session_id,
        handshake.drain,
        handshake.protocol_version,
        handshake.capabilities.to_vec(),
        handshake.policy_flags.to_vec(),
    ));
}

fn persist_session_handshake_to_workspace(ctx: &mut PhaseContext<'_>, session: &AgentSession) {
    let ts = current_unix_timestamp();
    let caps = join_identifiers(session.capabilities.iter().map(|c| c.identifier()));
    let flags = join_identifiers(session.policy_flags.iter().map(|f| f.identifier()));
    if let Err(e) = persist_session_handshake(
        ctx.workspace,
        &session.session_id,
        ts,
        session.drain.as_str(),
        &session.protocol_version,
        &caps,
        &flags,
    ) {
        ctx.logger.warn(&format!(
            "Failed to persist session handshake for session {}: {}",
            session.session_id, e
        ));
    }
}

fn current_unix_timestamp() -> u64 {
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn join_identifiers<'a>(iter: impl Iterator<Item = &'a str>) -> String {
    iter.collect::<Vec<_>>().join(",")
}

fn build_audit_records_for_session(session: &AgentSession, timestamp: u64) -> Vec<AuditRecord> {
    session
        .capabilities
        .iter()
        .map(|cap| {
            let outcome = session.check_capability(cap);
            let description = describe_capability_outcome(cap, session, &outcome);
            AuditRecord::new(
                session.session_id.clone(),
                timestamp,
                cap,
                outcome,
                description,
            )
        })
        .collect()
}

fn describe_capability_outcome(
    cap: Capability,
    session: &AgentSession,
    outcome: &PolicyOutcome,
) -> String {
    match outcome {
        PolicyOutcome::Approved => format!(
            "Capability {} granted for {} drain via session handshake",
            cap.identifier(),
            session.drain
        ),
        PolicyOutcome::Denied { reason } => reason.clone(),
        PolicyOutcome::ApprovedWithRestriction { restriction } => format!(
            "Capability {} approved with restriction: {}",
            cap.identifier(),
            restriction
        ),
    }
}

fn build_handshake_audit_record(session: &AgentSession, timestamp: u64) -> AuditRecord {
    let caps = join_identifiers(session.capabilities.iter().map(|c| c.identifier()));
    let flags = join_identifiers(session.policy_flags.iter().map(|f| f.identifier()));
    let description = format!(
        "Session handshake: drain={}, protocol={}, capabilities=[{}], policy_flags=[{}]",
        session.drain.as_str(),
        session.protocol_version,
        caps,
        flags,
    );
    AuditRecord::new(
        session.session_id.clone(),
        timestamp,
        Capability::EnvRead,
        PolicyOutcome::Approved,
        description,
    )
}

fn build_and_merge_audit_trail(ctx: &mut PhaseContext<'_>, session: &AgentSession) {
    let timestamp = current_unix_timestamp();
    let audit_records = build_audit_records_for_session(session, timestamp);
    let mut audit_trail = AuditTrail::from_records(audit_records);
    audit_trail = audit_trail.record_capability_injection(
        &session.session_id,
        timestamp,
        &session.capabilities,
    );
    ctx.logger.info(&format!(
        "RFC-009 audit trail: {} records for session {}",
        audit_trail.len(),
        session.session_id,
    ));
    let handshake_record = build_handshake_audit_record(session, timestamp);
    let combined_records: Vec<_> = std::iter::once(handshake_record)
        .chain(ctx.audit_trail.records().iter().cloned())
        .chain(audit_trail.records().iter().cloned())
        .collect();
    ctx.audit_trail = AuditTrail::from_records(combined_records);
}

fn start_mcp_bridge_for_session(
    ctx: &mut PhaseContext<'_>,
    session: &AgentSession,
) -> Result<(SessionBridge, Option<String>)> {
    let workspace_arc = Arc::clone(&ctx.workspace_arc);
    let bridge = crate::phases::commit::start_mcp_bridge(session.clone(), workspace_arc)
        .map_err(|e| {
            anyhow::anyhow!(
                "MCP bridge startup failed for session {} (drain={}): {}. MCP is mandatory and execution was aborted.",
                session.session_id,
                session.drain.as_str(),
                e
            )
        })?;
    let uri = bridge.endpoint_uri();
    ctx.logger.info(&format!(
        "RFC-009 MCP endpoint prepared: {} (socket: {})",
        uri,
        bridge.socket_path().display()
    ));
    Ok((bridge, Some(uri)))
}

fn drain_and_merge_mcp_audit_records(
    ctx: &mut PhaseContext<'_>,
    session_bridge: &mut SessionBridge,
) {
    session_bridge.shutdown();

    // Drain MCP audit records accumulated during agent execution and merge into
    // the phase context's audit trail. This must be called after the MCP server
    // has finished serving (agent execution complete, bridge shut down) to ensure
    // all records have been captured.
    let new_mcp_records = session_bridge.drain_audit_records();
    if new_mcp_records.is_empty() {
        return;
    }

    // Merge into ctx.audit_trail: chain existing records with new MCP records
    let existing_records = ctx.audit_trail.records().iter().cloned();
    ctx.audit_trail = AuditTrail::from_records(existing_records.chain(new_mcp_records));
}

fn execution_result_status(event: &Result<PipelineEvent>) -> &'static str {
    match event {
        Ok(PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })) => "success",
        Ok(PipelineEvent::Agent(AgentEvent::InvocationFailed { .. })) => "failure",
        Ok(PipelineEvent::Agent(AgentEvent::CapabilityDenied { .. })) => "denied",
        Ok(_) => "success",
        Err(_) => "failure",
    }
}

fn record_execution_telemetry_if_needed(
    ctx: &mut PhaseContext<'_>,
    session: &AgentSession,
    event: &Result<PipelineEvent>,
    execution_start: SystemTime,
    role: AgentRole,
) {
    let execution_duration_ms = execution_start
        .elapsed()
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    if execution_duration_ms == 0 {
        return;
    }
    let effect_name = format!("{:?}", role);
    let timestamp = current_unix_timestamp();
    let result_status = execution_result_status(event);
    ctx.audit_trail = record_execution_telemetry(
        &ctx.audit_trail,
        &session.session_id,
        timestamp,
        &effect_name,
        execution_duration_ms,
        result_status,
    );
}

fn persist_audit_trail_to_workspace(ctx: &mut PhaseContext<'_>, session: &AgentSession) {
    if let Err(e) = persist_audit_trail(ctx.workspace, &session.session_id, &ctx.audit_trail) {
        ctx.logger.warn(&format!(
            "Failed to persist audit trail for session {}: {}",
            session.session_id, e
        ));
    }
}
