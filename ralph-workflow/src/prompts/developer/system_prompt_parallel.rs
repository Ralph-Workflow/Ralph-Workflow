// System prompt template and generation (parallel execution).
//
// Contains functions for generating parallel planning, worker, and verifier prompts.

/// Generate parallel planning prompt using template registry.
///
/// This prompt instructs the planning agent to create a parallel implementation
/// plan with work unit decomposition and non-overlapping edit areas.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `prompt_content` - The original user request (PROMPT.md content)
/// * `workspace` - Workspace for resolving absolute paths
/// * `session_caps` - Bundled session capabilities and policy flags
#[must_use]
pub fn prompt_parallel_planning_with_context(
    context: &TemplateContext,
    prompt_content: Option<&str>,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    let partials = get_shared_partials();

    let template_content = context
        .registry()
        .get_template("parallel_planning")
        .unwrap_or_else(|_| {
            // Fallback to embedded template if registry fails
            ralph_workflow_policy::PARALLEL_PLANNING_TEMPLATE.to_string()
        });
    let template = Template::new(&template_content);
    let prompt_md = prompt_content.unwrap_or("No requirements provided");

    // Base variables for parallel planning prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PROMPT", prompt_md.to_string()),
        (
            "PLAN_XML_PATH",
            workspace.absolute_str(".agent/tmp/plan.xml"),
        ),
        (
            "PLAN_XSD_PATH",
            workspace.absolute_str(".agent/tmp/plan.xsd"),
        ),
    ]);

    // Compute capability variables from session capabilities
    let capability_vars =
        capability_template_variables(session_caps.capabilities, session_caps.policy_flags);

    // Merge base and capability variables using functional style (no mutation)
    let variables: HashMap<String, String> = base_vars
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(capability_vars)
        .collect();

    // Convert to HashMap<&str, String> for rendering
    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    template
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            // Embedded fallback template
            format!(
                "PARALLEL PLANNING MODE\n\nCreate an implementation plan for:\n\n{prompt_md}\n\n\
                 Decompose into parallel work units with non-overlapping edit areas.\n\n\
                 Output format: <ralph-plan><ralph-summary>...</ralph-summary>\
                 <ralph-parallel-plan><work-unit id=\"unit-1\">...</work-unit></ralph-parallel-plan>\
                 </ralph-plan>\n"
            )
        })
}

/// Generate parallel development worker prompt for a specific work unit.
///
/// This prompt scopes a development worker to its assigned edit area and work unit.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `prompt_content` - The original user request
/// * `work_unit` - The work unit this worker is assigned to
/// * `workspace` - Workspace for resolving absolute paths
/// * `session_caps` - Bundled session capabilities and policy flags
#[must_use]
pub fn prompt_parallel_dev_worker_with_context(
    context: &TemplateContext,
    prompt_content: Option<&str>,
    work_unit: &WorkUnit,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    let partials = get_shared_partials();

    let template_content = context
        .registry()
        .get_template("parallel_dev_worker")
        .unwrap_or_else(|_| {
            // Fallback to embedded template if registry fails
            ralph_workflow_policy::PARALLEL_DEV_WORKER_TEMPLATE.to_string()
        });
    let template = Template::new(&template_content);
    let prompt_md = prompt_content.unwrap_or("No requirements provided");

    // Format edit area paths as newline-separated list
    let edit_area_paths = if work_unit.edit_area.allowed_paths.is_empty() {
        "None".to_string()
    } else {
        work_unit
            .edit_area
            .allowed_paths
            .iter()
            .map(|p| format!("  - {}", p))
            .collect::<Vec<_>>()
            .join("\n")
    };

    // Format edit area directories as newline-separated list
    let edit_area_directories = if work_unit.edit_area.allowed_directories.is_empty() {
        "None".to_string()
    } else {
        work_unit
            .edit_area
            .allowed_directories
            .iter()
            .map(|d| format!("  - {}", d))
            .collect::<Vec<_>>()
            .join("\n")
    };

    // Base variables for parallel dev worker prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PROMPT", prompt_md.to_string()),
        ("WORK_UNIT_ID", work_unit.unit_id.clone()),
        ("WORK_UNIT_DESCRIPTION", work_unit.description.clone()),
        ("EDIT_AREA_PATHS", edit_area_paths.clone()),
        ("EDIT_AREA_DIRECTORIES", edit_area_directories.clone()),
        (
            "PLAN_XML_PATH",
            workspace.absolute_str(".agent/tmp/plan.xml"),
        ),
        (
            "PLAN_XSD_PATH",
            workspace.absolute_str(".agent/tmp/plan.xsd"),
        ),
    ]);

    // Compute capability variables from session capabilities
    let capability_vars =
        capability_template_variables(session_caps.capabilities, session_caps.policy_flags);

    // Merge base and capability variables using functional style (no mutation)
    let variables: HashMap<String, String> = base_vars
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(capability_vars)
        .collect();

    // Convert to HashMap<&str, String> for rendering
    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    template
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            // Embedded fallback template
            format!(
                "PARALLEL DEVELOPMENT WORKER - {}\n\n{}\n\n\
                 Edit Area:\n  Paths:\n{}\n  Directories:\n{}\n\n\
                 Output your implementation plan to .agent/tmp/plan.xml",
                work_unit.unit_id,
                work_unit.description,
                edit_area_paths,
                edit_area_directories
            )
        })
}

/// Generate parallel verifier/reconciler prompt.
///
/// This prompt instructs the verifier to review all worker outputs and make
/// a reconciliation decision.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `worker_outputs` - Summary of all worker outputs
/// * `all_completed` - Whether all workers completed successfully
/// * `workspace` - Workspace for resolving absolute paths
/// * `session_caps` - Bundled session capabilities and policy flags
#[must_use]
pub fn prompt_parallel_verifier_with_context(
    context: &TemplateContext,
    worker_outputs: &str,
    all_completed: bool,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    let partials = get_shared_partials();

    let template_content = context
        .registry()
        .get_template("parallel_verifier")
        .unwrap_or_else(|_| {
            // Fallback to embedded template if registry fails
            ralph_workflow_policy::PARALLEL_VERIFIER_TEMPLATE.to_string()
        });
    let template = Template::new(&template_content);

    // Base variables for parallel verifier prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("WORKER_OUTPUTS", worker_outputs.to_string()),
        (
            "ALL_WORK_UNITS_COMPLETED",
            if all_completed {
                "true".to_string()
            } else {
                "false".to_string()
            },
        ),
        (
            "VERDICT_XML_PATH",
            workspace.absolute_str(".agent/tmp/verdict.xml"),
        ),
    ]);

    // Compute capability variables from session capabilities
    let capability_vars =
        capability_template_variables(session_caps.capabilities, session_caps.policy_flags);

    // Merge base and capability variables using functional style (no mutation)
    let variables: HashMap<String, String> = base_vars
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(capability_vars)
        .collect();

    // Convert to HashMap<&str, String> for rendering
    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    template
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            // Embedded fallback template
            format!(
                "VERIFIER/RECONCILER AGENT\n\n\
                 Review parallel worker outputs and make a reconciliation decision.\n\n\
                 Worker Outputs:\n{}\n\n\
                 All workers completed: {}\n\n\
                 Decision options: accept, rework, spawn-new, collapse-to-single\n\n\
                 Output your verdict to .agent/tmp/verdict.xml",
                worker_outputs,
                if all_completed { "Yes" } else { "No" }
            )
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::parallel::RestrictedEditArea;
    use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
    use crate::prompts::SessionCapabilities;
    use crate::workspace::WorkspaceFs;
    use std::env;

    fn test_workspace() -> WorkspaceFs {
        WorkspaceFs::new(env::current_dir().unwrap())
    }

    #[test]
    fn parallel_planning_prompt_includes_parallel_instructions() {
        let workspace = test_workspace();
        let context = TemplateContext::default();

        let prompt = prompt_parallel_planning_with_context(
            &context,
            Some("Implement feature X"),
            &workspace,
            SessionCapabilities::new(
                &CapabilitySet::defaults_for_drain(SessionDrain::Development),
                &PolicyFlagSet::defaults_for_drain(SessionDrain::Development),
            ),
        );

        // Should include parallel planning instructions
        assert!(prompt.contains("PARALLEL PLANNING MODE"));
        assert!(prompt.contains("work unit"));
        assert!(prompt.contains("edit area"));
    }

    #[test]
    fn parallel_dev_worker_prompt_includes_work_unit_info() {
        let workspace = test_workspace();
        let context = TemplateContext::default();

        let work_unit = WorkUnit {
            unit_id: "unit-1".to_string(),
            description: "Implement feature A".to_string(),
            edit_area: RestrictedEditArea::paths(vec!["src/a.rs".to_string()]),
            dependencies: Vec::new(),
        };

        let prompt = prompt_parallel_dev_worker_with_context(
            &context,
            Some("Implement feature X"),
            &work_unit,
            &workspace,
            SessionCapabilities::new(
                &CapabilitySet::defaults_for_drain(SessionDrain::Development),
                &PolicyFlagSet::defaults_for_drain(SessionDrain::Development),
            ),
        );

        // Should include work unit info
        assert!(prompt.contains("unit-1"));
        assert!(prompt.contains("Implement feature A"));
        assert!(prompt.contains("src/a.rs"));
    }

    #[test]
    fn parallel_verifier_prompt_includes_worker_outputs() {
        let workspace = test_workspace();
        let context = TemplateContext::default();

        let worker_outputs = "Worker 1: Completed\nWorker 2: Completed";
        let prompt = prompt_parallel_verifier_with_context(
            &context,
            worker_outputs,
            true,
            &workspace,
            SessionCapabilities::new(
                &CapabilitySet::defaults_for_drain(SessionDrain::Development),
                &PolicyFlagSet::defaults_for_drain(SessionDrain::Development),
            ),
        );

        // Should include worker outputs and decision options
        assert!(prompt.contains("Worker 1"));
        assert!(prompt.contains("Worker 2"));
        assert!(prompt.contains("VERIFIER"));
    }
}