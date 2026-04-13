// Tests for state module.

#[cfg(test)]
mod tests {
    use super::*;
    use crate::checkpoint::state::{AgentConfigSnapshot, CheckpointParams, CliArgsSnapshot};

    include!("io_tests/core_state.rs");
    include!("io_tests/continuation_state.rs");
    include!("io_tests/fix_status.rs");
    include!("io_tests/consumer_signature.rs");
}
