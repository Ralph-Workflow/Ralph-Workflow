// Health module tests.

#[cfg(test)]
mod tests {
    use crate::json_parser::health::{
        HealthMonitor, ParserHealth, StreamingPattern, StreamingQualityMetrics,
    };
    use crate::logger::Colors;

    include!("tests/parser_health.rs");
    include!("tests/streaming_quality_metrics.rs");
}
