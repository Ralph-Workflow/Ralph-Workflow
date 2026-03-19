//! Planning phase effect-to-event mapping.
//!
//! This module handles effect execution for the Planning phase of the pipeline.
//! Planning involves generating an implementation plan from the user's requirements.
//!
//! ## Planning Phase Flow
//!
//! 1. **`InitializeAgentChain`** - Set up the agent chain for planning
//! 2. **`PreparePlanningPrompt`** - Generate the planning prompt
//! 3. **`MaterializePlanningInputs`** - Prepare inputs for the planning agent
//! 4. **`CleanupRequiredFiles`** - Clean any existing XML (handled in `lifecycle_effects`)
//! 5. **`InvokePlanningAgent`** - Execute the planning agent
//! 6. **`ExtractPlanningXml`** - Extract XML from agent output
//! 7. **`ValidatePlanningXml`** - Validate XML against XSD schema
//! 8. **`WritePlanningMarkdown`** - Convert XML to markdown for reference
//! 9. **`ArchivePlanningXml`** - Archive the XML for audit trail
//! 10. **`ApplyPlanningOutcome`** - Apply the plan to state and transition to Development
//!
//! ## Mock Behavior
//!
//! All effects return deterministic mock events without performing real I/O.
//! The mock plan XML includes realistic structure (summary, steps, files, risks)
//! to ensure downstream code can process it correctly.

use crate::reducer::effect::Effect;
use crate::reducer::event::{PipelineEvent, PipelinePhase};
use crate::reducer::state::{
    MaterializedPromptInput, PromptInputKind, PromptInputRepresentation,
    PromptMaterializationReason,
};
use crate::reducer::ui_event::{UIEvent, XmlOutputContext, XmlOutputType};

use super::super::MockEffectHandler;

impl MockEffectHandler {
    /// Handle planning phase effects.
    ///
    /// Returns appropriate mock events for each planning effect without
    /// performing real agent execution, XML validation, or file I/O.
    pub(super) fn handle_planning_effect(
        &self,
        effect: &Effect,
    ) -> Option<(PipelineEvent, Vec<UIEvent>)> {
        match *effect {
            Effect::PreparePlanningPrompt {
                iteration,
                prompt_mode: _,
            } => Some((PipelineEvent::planning_prompt_prepared(iteration), vec![])),

            Effect::MaterializePlanningInputs { iteration } => Some((
                PipelineEvent::planning_inputs_materialized(
                    iteration,
                    MaterializedPromptInput {
                        kind: PromptInputKind::Prompt,
                        content_id_sha256: "id".to_string(),
                        consumer_signature_sha256: self
                            .state
                            .agent_chain
                            .consumer_signature_sha256(),
                        original_bytes: 1,
                        final_bytes: 1,
                        model_budget_bytes: None,
                        inline_budget_bytes: None,
                        representation: PromptInputRepresentation::Inline,
                        reason: PromptMaterializationReason::WithinBudgets,
                    },
                ),
                vec![],
            )),

            Effect::InvokePlanningAgent { iteration } => {
                Some((PipelineEvent::planning_agent_invoked(iteration), vec![]))
            }

            Effect::ExtractPlanningXml { iteration } => {
                Some((PipelineEvent::planning_xml_extracted(iteration), vec![]))
            }

            Effect::ValidatePlanningXml { iteration } => {
                let mock_plan_xml = r#"<ralph-plan>
 <ralph-summary>
 <context>Mock plan for testing</context>
 <scope-items>
 <scope-item count="1">test item</scope-item>
 <scope-item count="1">another item</scope-item>
 <scope-item count="1">third item</scope-item>
 </scope-items>
 </ralph-summary>
 <ralph-implementation-steps>
 <step number="1" type="file-change">
 <title>Mock step</title>
 <target-files><file path="src/test.rs" action="modify"/></target-files>
 <content><paragraph>Test content</paragraph></content>
 </step>
 </ralph-implementation-steps>
 <ralph-critical-files>
 <primary-files><file path="src/test.rs" action="modify"/></primary-files>
 <reference-files><file path="src/lib.rs" purpose="reference"/></reference-files>
 </ralph-critical-files>
 <ralph-risks-mitigations>
 <risk-pair severity="low"><risk>Test risk</risk><mitigation>Test mitigation</mitigation></risk-pair>
 </ralph-risks-mitigations>
 <ralph-verification-strategy>
 <verification><method>Test method</method><expected-outcome>Pass</expected-outcome></verification>
 </ralph-verification-strategy>
 </ralph-plan>"#;
                let ui = vec![UIEvent::XmlOutput {
                    xml_type: XmlOutputType::DevelopmentPlan,
                    content: mock_plan_xml.to_string(),
                    context: Some(XmlOutputContext {
                        iteration: Some(iteration),
                        pass: None,
                        snippets: Vec::new(),
                    }),
                }];
                let markdown = "# Plan\n\n- Mock step\n".to_string();
                Some((
                    PipelineEvent::planning_xml_validated(iteration, true, Some(markdown)),
                    ui,
                ))
            }

            Effect::WritePlanningMarkdown { iteration } => {
                Some((PipelineEvent::planning_markdown_written(iteration), vec![]))
            }

            Effect::ArchivePlanningXml { iteration } => {
                Some((PipelineEvent::planning_xml_archived(iteration), vec![]))
            }

            Effect::ApplyPlanningOutcome { iteration, valid } => {
                let ui = if valid {
                    vec![UIEvent::PhaseTransition {
                        from: Some(self.state.phase),
                        to: PipelinePhase::Development,
                    }]
                } else {
                    vec![]
                };
                Some((
                    PipelineEvent::plan_generation_completed(iteration, valid),
                    ui,
                ))
            }

            _ => None,
        }
    }
}
