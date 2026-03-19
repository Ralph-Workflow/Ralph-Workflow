use super::common::TestFixture;
use crate::reducer::event::PipelineEvent;
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::PipelineState;

#[test]
fn test_extract_planning_xml_emits_missing_event() {
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    let result = handler.extract_planning_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Planning(crate::reducer::event::PlanningEvent::PlanXmlMissing { .. })
    ));
}

#[test]
fn test_extract_development_xml_emits_missing_event() {
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    let result = handler.extract_development_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Development(crate::reducer::event::DevelopmentEvent::XmlMissing { .. })
    ));
}

#[test]
fn test_extract_review_issues_xml_emits_missing_event() {
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let result = handler.extract_review_issues_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::IssuesXmlMissing { .. })
    ));
}

#[test]
fn test_extract_fix_result_xml_emits_missing_event() {
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let result = handler.extract_fix_result_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::FixResultXmlMissing { .. })
    ));
}
