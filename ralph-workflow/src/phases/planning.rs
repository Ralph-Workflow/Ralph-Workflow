use crate::reducer::prompt_inputs::sha256_hex_str;

pub(crate) fn apply_same_agent_retry_preamble(retry_preamble: &str, base_prompt: &str) -> String {
    format!("{retry_preamble}\n{base_prompt}")
}

pub(crate) fn planning_prompt_content_id(
    mode_prefix: &str,
    content_id_sha256: &str,
    consumer_signature_sha256: &str,
) -> String {
    sha256_hex_str(&format!(
        "{mode_prefix}:prompt:{content_id_sha256}:consumer:{consumer_signature_sha256}"
    ))
}

pub(crate) fn planning_xsd_retry_prompt_content_id(
    last_output_content_id_sha256: &str,
    consumer_signature_sha256: &str,
) -> String {
    sha256_hex_str(&format!(
        "planning_xsd_retry:last_output:{last_output_content_id_sha256}:consumer:{consumer_signature_sha256}"
    ))
}

#[cfg(test)]
mod tests {
    use super::{
        apply_same_agent_retry_preamble, planning_prompt_content_id,
        planning_xsd_retry_prompt_content_id,
    };
    use crate::files::llm_output_extraction::validate_plan_xml;
    use crate::phases::development::format_plan_as_markdown;
    use crate::reducer::prompt_inputs::sha256_hex_str;

    #[test]
    fn parse_planning_markdown_extracts_markdown_from_valid_xml() {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Add a new feature</context>
<scope-items>
<scope-item count="1" category="files">files to modify</scope-item>
<scope-item count="2" category="functions">functions to update</scope-item>
<scope-item count="1" category="tests">tests to add</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Do the thing</title>
<content>
<paragraph>Implement update</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="README.md" action="modify"/>
</primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="low">
<risk>Minor mismatch</risk>
<mitigation>Run tests</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>Run unit tests</method>
<expected-outcome>All tests pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let elements = validate_plan_xml(xml).expect("valid XML should parse");
        let markdown = format_plan_as_markdown(&elements);

        assert!(markdown.contains("## Summary"));
        assert!(markdown.contains("Do the thing"));
    }

    #[test]
    fn apply_same_agent_retry_preamble_joins_preamble_and_prompt() {
        let actual = apply_same_agent_retry_preamble("Retry carefully", "Base prompt");
        assert_eq!(actual, "Retry carefully\nBase prompt");
    }

    #[test]
    fn planning_prompt_content_id_hashes_mode_content_and_consumer() {
        let actual = planning_prompt_content_id("planning_normal", "abc123", "sig789");
        let expected = sha256_hex_str("planning_normal:prompt:abc123:consumer:sig789");
        assert_eq!(actual, expected);
    }

    #[test]
    fn planning_xsd_retry_prompt_content_id_hashes_last_output_and_consumer() {
        let actual = planning_xsd_retry_prompt_content_id("out123", "sig789");
        let expected = sha256_hex_str("planning_xsd_retry:last_output:out123:consumer:sig789");
        assert_eq!(actual, expected);
    }
}
