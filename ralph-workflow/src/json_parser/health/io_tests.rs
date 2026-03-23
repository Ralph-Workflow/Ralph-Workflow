// Health module tests.

#[cfg(test)]
mod tests {
    use crate::json_parser::health::{
        HealthMonitor, ParserHealth, StreamingPattern, StreamingQualityMetrics,
    };
    use crate::logger::Colors;

    include!("io_tests/parser_health.rs");
    include!("io_tests/streaming_quality_metrics.rs");
}
