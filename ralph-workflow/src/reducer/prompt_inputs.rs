use sha2::{Digest, Sha256};

#[must_use]
pub fn sha256_hex_bytes(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

#[must_use]
pub fn sha256_hex_str(s: &str) -> String {
    sha256_hex_bytes(s.as_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sha256_hex_str_is_deterministic() {
        let content = "x".repeat(10_000);
        let id1 = sha256_hex_str(&content);
        let id2 = sha256_hex_str(&content);
        let id3 = sha256_hex_str(&content);

        assert_eq!(id1, id2);
        assert_eq!(id2, id3);
        assert_eq!(id1.len(), 64, "sha256 hex digest should be 64 chars");
    }

    #[test]
    fn sha256_hex_str_differs_for_different_inputs() {
        let id1 = sha256_hex_str("content version 1");
        let id2 = sha256_hex_str("content version 2");
        assert_ne!(id1, id2);
    }
}
