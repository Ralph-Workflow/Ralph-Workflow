use super::super::common::TestFixture;
use crate::reducer::event::PipelineEvent;
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{AgentChainState, CommitState, PipelineState};

#[test]
fn test_extract_commit_xml_emits_missing_event_when_absent() {
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };

    let result = handler.extract_commit_xml(&ctx);

    assert!(matches!(
        result.event,
        PipelineEvent::Commit(crate::reducer::event::CommitEvent::CommitXmlMissing { attempt: 1 })
    ));
}
