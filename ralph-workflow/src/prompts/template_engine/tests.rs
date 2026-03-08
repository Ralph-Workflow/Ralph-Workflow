// Tests for the template engine.

#[cfg(test)]
mod tests {
    use super::*;

    include!("tests_basic.rs");
    include!("tests_partials.rs");
    include!("tests_log.rs");
}
