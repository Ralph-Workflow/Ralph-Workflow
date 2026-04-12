//! Snapshot tests for audit record NDJSON serialization.
//!
//! These tests verify that audit records serialize to the correct NDJSON format
//! for persistence to `.agent/audit/{session_id}.jsonl` files.

#[cfg(test)]
mod tests {
    use crate::agents::session::audit::{AuditRecordType, PersistedAuditRecord};
    use crate::agents::session::{AuditRecord, Capability, PolicyOutcome};

    /// Verify session handshake record serializes correctly.
    #[test]
    fn snapshot_session_handshake_record() {
        let record = PersistedAuditRecord {
            session_id: "test-run-planning-1".to_string(),
            timestamp: 1700000000,
            record_type: AuditRecordType::CapabilityInjection,
            capability: "session_handshake".to_string(),
            outcome: "approved".to_string(),
            description: "Session handshake: drain=planning, protocol=1.0, capabilities=[workspace.read,artifact.submit,run.report_progress,git.status_read,git.diff_read], policy_flags=[]".to_string(),
            effect_name: None,
            command: None,
            duration_ms: None,
            result_status: None,
            run_id: None,
            generation: None,
            drain: None,
            policy_mode: None,
            event_type: None,
        };

        let json = serde_json::to_string(&record).expect("should serialize");
        let parsed: serde_json::Value = serde_json::from_str(&json).expect("should parse as JSON");

        // Verify structure
        assert_eq!(parsed["session_id"], "test-run-planning-1");
        assert_eq!(parsed["timestamp"], 1700000000);
        assert_eq!(parsed["record_type"], "capability_injection");
        assert_eq!(parsed["capability"], "session_handshake");
        assert_eq!(parsed["outcome"], "approved");
        assert!(parsed["description"].is_string());
        assert!(parsed["effect_name"].is_null());
        assert!(parsed["command"].is_null());
    }

    /// Verify capability check approved record serializes correctly.
    #[test]
    fn snapshot_capability_check_approved() {
        let record = PersistedAuditRecord {
            session_id: "test-run-planning-1".to_string(),
            timestamp: 1700000001,
            record_type: AuditRecordType::CapabilityCheck,
            capability: "workspace.read".to_string(),
            outcome: "approved".to_string(),
            description:
                "Capability workspace.read granted for planning drain via session handshake"
                    .to_string(),
            effect_name: Some("InvokePlanningAgent".to_string()),
            command: None,
            duration_ms: None,
            result_status: None,
            run_id: None,
            generation: None,
            drain: None,
            policy_mode: None,
            event_type: None,
        };

        let json = serde_json::to_string(&record).expect("should serialize");
        let parsed: serde_json::Value = serde_json::from_str(&json).expect("should parse as JSON");

        assert_eq!(parsed["session_id"], "test-run-planning-1");
        assert_eq!(parsed["record_type"], "capability_check");
        assert_eq!(parsed["capability"], "workspace.read");
        assert_eq!(parsed["outcome"], "approved");
        assert_eq!(parsed["effect_name"], "InvokePlanningAgent");
        assert!(parsed["command"].is_null());
    }

    /// Verify capability check denied record serializes correctly.
    #[test]
    fn snapshot_capability_check_denied() {
        let record = PersistedAuditRecord {
            session_id: "test-run-planning-1".to_string(),
            timestamp: 1700000002,
            record_type: AuditRecordType::CapabilityCheck,
            capability: "git.write".to_string(),
            outcome: "denied".to_string(),
            description:
                "Capability git.write denied: planning drain does not have git.write capability"
                    .to_string(),
            effect_name: Some("CreateCommit".to_string()),
            command: None,
            duration_ms: None,
            result_status: None,
            run_id: None,
            generation: None,
            drain: None,
            policy_mode: None,
            event_type: None,
        };

        let json = serde_json::to_string(&record).expect("should serialize");
        let parsed: serde_json::Value = serde_json::from_str(&json).expect("should parse as JSON");

        assert_eq!(parsed["session_id"], "test-run-planning-1");
        assert_eq!(parsed["record_type"], "capability_check");
        assert_eq!(parsed["capability"], "git.write");
        assert_eq!(parsed["outcome"], "denied");
        assert!(parsed["description"].as_str().unwrap().contains("denied"));
    }

    /// Verify command check record serializes correctly.
    #[test]
    fn snapshot_command_check_record() {
        let record = PersistedAuditRecord {
            session_id: "test-run-development-1".to_string(),
            timestamp: 1700000003,
            record_type: AuditRecordType::CommandCheck,
            capability: "process.exec_bounded".to_string(),
            outcome: "denied".to_string(),
            description: "Command 'git' is blacklisted: version control commands must go through Ralph's git capabilities".to_string(),
            effect_name: None,
            command: Some("git commit -m \"fix\"".to_string()),
            duration_ms: None,
            result_status: None,
            run_id: None,
            generation: None,
            drain: None,
            policy_mode: None,
            event_type: None,
        };

        let json = serde_json::to_string(&record).expect("should serialize");
        let parsed: serde_json::Value = serde_json::from_str(&json).expect("should parse as JSON");

        assert_eq!(parsed["session_id"], "test-run-development-1");
        assert_eq!(parsed["record_type"], "command_check");
        assert_eq!(parsed["capability"], "process.exec_bounded");
        assert_eq!(parsed["outcome"], "denied");
        assert!(parsed["description"]
            .as_str()
            .unwrap()
            .contains("blacklisted"));
        assert_eq!(parsed["command"], "git commit -m \"fix\"");
    }

    /// Verify capability injection record serializes correctly.
    #[test]
    fn snapshot_capability_injection_record() {
        let record = PersistedAuditRecord {
            session_id: "test-run-planning-1".to_string(),
            timestamp: 1700000004,
            record_type: AuditRecordType::CapabilityInjection,
            capability: "capability_injection".to_string(),
            outcome: "approved".to_string(),
            description: "Capabilities injected into prompt template: workspace.read, artifact.submit, run.report_progress, git.status_read, git.diff_read".to_string(),
            effect_name: None,
            command: None,
            duration_ms: None,
            result_status: None,
            run_id: None,
            generation: None,
            drain: None,
            policy_mode: None,
            event_type: None,
        };

        let json = serde_json::to_string(&record).expect("should serialize");
        let parsed: serde_json::Value = serde_json::from_str(&json).expect("should parse as JSON");

        assert_eq!(parsed["record_type"], "capability_injection");
        assert!(parsed["description"]
            .as_str()
            .unwrap()
            .contains("Capabilities injected"));
    }

    /// Verify full audit trail serializes to valid NDJSON (multiple records).
    #[test]
    fn snapshot_full_audit_trail_ndjson() {
        let records = [
            PersistedAuditRecord {
                session_id: "test-run-planning-1".to_string(),
                timestamp: 1700000000,
                record_type: AuditRecordType::CapabilityInjection,
                capability: "session_handshake".to_string(),
                outcome: "approved".to_string(),
                description: "Session handshake: drain=planning".to_string(),
                effect_name: None,
                command: None,
                duration_ms: None,
                result_status: None,
                run_id: None,
                generation: None,
                drain: None,
                policy_mode: None,
                event_type: None,
            },
            PersistedAuditRecord {
                session_id: "test-run-planning-1".to_string(),
                timestamp: 1700000001,
                record_type: AuditRecordType::CapabilityCheck,
                capability: "workspace.read".to_string(),
                outcome: "approved".to_string(),
                description: "Capability granted".to_string(),
                effect_name: Some("InvokePlanningAgent".to_string()),
                command: None,
                duration_ms: None,
                result_status: None,
                run_id: None,
                generation: None,
                drain: None,
                policy_mode: None,
                event_type: None,
            },
        ];

        // Serialize each record as a separate JSON line
        let ndjson: String = records
            .iter()
            .map(|r| serde_json::to_string(r).expect("should serialize"))
            .collect::<Vec<_>>()
            .join("\n");

        // Parse each line separately (NDJSON format)
        let lines: Vec<&str> = ndjson.lines().collect();
        assert_eq!(lines.len(), 2);

        let parsed1: serde_json::Value =
            serde_json::from_str(lines[0]).expect("first line should be valid JSON");
        assert_eq!(parsed1["record_type"], "capability_injection");

        let parsed2: serde_json::Value =
            serde_json::from_str(lines[1]).expect("second line should be valid JSON");
        assert_eq!(parsed2["record_type"], "capability_check");
    }

    /// Verify AuditRecord converts correctly to PersistedAuditRecord.
    #[test]
    fn audit_record_to_persisted() {
        let session_id = crate::agents::session::AgentSessionId::new(
            "test-run",
            &crate::agents::session::SessionDrain::Planning,
            1,
        );
        let record = AuditRecord::new(
            session_id,
            1700000000,
            Capability::WorkspaceRead,
            PolicyOutcome::Approved,
            "Test capability check".to_string(),
        );

        let persisted: PersistedAuditRecord = (&record).into();
        let json = serde_json::to_string(&persisted).expect("should serialize");
        let parsed: serde_json::Value = serde_json::from_str(&json).expect("should parse");

        assert_eq!(parsed["capability"], "workspace.read");
        assert_eq!(parsed["outcome"], "approved");
    }

    /// Verify denied AuditRecord converts with "denied" outcome.
    #[test]
    fn audit_record_denied_to_persisted() {
        let session_id = crate::agents::session::AgentSessionId::new(
            "test-run",
            &crate::agents::session::SessionDrain::Planning,
            1,
        );
        let record = AuditRecord::new(
            session_id,
            1700000000,
            Capability::GitWrite,
            PolicyOutcome::Denied {
                reason: "Planning drain does not have GitWrite".to_string(),
            },
            "Capability denied".to_string(),
        );

        let persisted: PersistedAuditRecord = (&record).into();
        let json = serde_json::to_string(&persisted).expect("should serialize");
        let parsed: serde_json::Value = serde_json::from_str(&json).expect("should parse");

        assert_eq!(parsed["capability"], "git.write");
        assert_eq!(parsed["outcome"], "denied");
        assert!(parsed["description"].as_str().unwrap().contains("denied"));
    }
}
