// Tests for XSD validation module.

#[cfg(test)]
mod tests {
    use super::*;

    include!("tests/format_for_ai_retry.rs");
    include!("tests/validate_xml_against_xsd.rs");
    include!("tests/commit_message_elements.rs");
    include!("tests/llm_realistic_outputs.rs");
    include!("tests/tolerant_parsing.rs");
}
