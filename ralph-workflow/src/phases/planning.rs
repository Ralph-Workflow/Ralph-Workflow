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

#[cfg(test)]
mod tests {
    use super::{apply_same_agent_retry_preamble, planning_prompt_content_id};
    use crate::reducer::prompt_inputs::sha256_hex_str;

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
}
