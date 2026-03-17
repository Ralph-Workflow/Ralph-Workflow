//! Shared boundary detection logic for all lints.
//!
//! Boundary modules are directories where effectful code (mutation, I/O) is
//! permitted. This mirrors the Haskell separation between pure computation
//! and the `IO` monad.
//!
//! This is pure domain logic - no I/O, no environment access.

use rustc_hir::intravisit::{walk_expr, Visitor};
use rustc_hir::{Body, Expr, ExprKind, MatchSource};
use rustc_lint::LintContext;
use rustc_span::{FileName, Span};
use std::path::{Component, Path};

pub const BOUNDARY_FUNCTION_LINE_THRESHOLD: usize = 12;
pub const BOUNDARY_FUNCTION_DECISION_THRESHOLD: usize = 2;
pub const BOUNDARY_FUNCTION_COMPLEXITY_THRESHOLD: usize = 6;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct BoundaryFunctionMetrics {
    pub line_count: usize,
    pub statement_count: usize,
    pub decision_points: usize,
    pub boolean_operators: usize,
    pub match_arms: usize,
    pub max_nesting_depth: usize,
}

fn line_complexity_points(line_count: usize) -> usize {
    match line_count {
        0..=11 => 0,
        12..=17 => 1,
        18..=24 => 2,
        _ => 3,
    }
}

fn statement_complexity_points(statement_count: usize) -> usize {
    match statement_count {
        0..=3 => 0,
        4..=6 => 1,
        7..=9 => 2,
        _ => 3,
    }
}

fn boolean_complexity_points(boolean_operators: usize) -> usize {
    match boolean_operators {
        0 => 0,
        1 => 1,
        _ => 2,
    }
}

fn match_arm_complexity_points(match_arms: usize) -> usize {
    match match_arms {
        0..=2 => 0,
        3..=4 => 1,
        _ => 2,
    }
}

fn nesting_complexity_points(max_nesting_depth: usize) -> usize {
    match max_nesting_depth {
        0..=1 => 0,
        2 => 1,
        _ => 2,
    }
}

/// Boundary module path components where effects are permitted.
///
/// Code in a directory whose path contains one of these components is exempt
/// from functional purity restrictions:
///
/// - `io/` — filesystem and external data transport
/// - `runtime/` — OS-facing capabilities (process, env, time)
/// - `ffi/` — foreign function interface bindings
/// - `boundary/` — thin composition seams between pure and effectful code
pub const BOUNDARY_MODULES: &[&str] = &["io", "runtime", "ffi", "boundary"];

/// Check whether a path contains a boundary module component.
///
/// Returns `true` if any path component (directory or file stem) exactly
/// matches one of the boundary markers. Substring matches are rejected:
/// `iostream/` does NOT match `io`.
pub fn path_contains_boundary_component(path: &Path) -> bool {
    path.components().any(|component| {
        matches!(component, Component::Normal(name) if {
            let name_str = name.to_str().unwrap_or("");
            let stem = name_str.strip_suffix(".rs").unwrap_or(name_str);
            BOUNDARY_MODULES.iter().any(|b| *b == stem)
        })
    })
}

/// Check whether a span is located in a boundary module.
///
/// This is the primary entry point for lints to check whether code at a
/// given span should be exempt from purity restrictions.
pub fn is_in_boundary_module<C: HasSourceMap>(cx: &C, span: rustc_span::Span) -> bool {
    let source_map = cx.source_map();
    let filename = source_map.span_to_filename(span);

    match &filename {
        FileName::Real(real_name) => real_name
            .local_path()
            .map_or(false, path_contains_boundary_component),
        _ => false,
    }
}

pub fn boundary_function_complexity_score(metrics: &BoundaryFunctionMetrics) -> usize {
    line_complexity_points(metrics.line_count)
        + statement_complexity_points(metrics.statement_count)
        + metrics.decision_points
        + boolean_complexity_points(metrics.boolean_operators)
        + match_arm_complexity_points(metrics.match_arms)
        + nesting_complexity_points(metrics.max_nesting_depth)
}

pub fn boundary_function_needs_split(metrics: &BoundaryFunctionMetrics) -> bool {
    let complexity_score = boundary_function_complexity_score(metrics);
    let has_policy_shape = metrics.decision_points >= BOUNDARY_FUNCTION_DECISION_THRESHOLD
        || metrics.max_nesting_depth >= 2
        || metrics.boolean_operators >= 2
        || metrics.match_arms >= 5;
    let is_not_tiny = metrics.line_count >= BOUNDARY_FUNCTION_LINE_THRESHOLD
        || metrics.max_nesting_depth >= 3
        || metrics.match_arms >= 5;

    has_policy_shape && is_not_tiny && complexity_score >= BOUNDARY_FUNCTION_COMPLEXITY_THRESHOLD
}

fn count_statements_in_body(body: &Body<'_>) -> usize {
    match body.value.kind {
        ExprKind::Block(block, _) => block.stmts.len() + usize::from(block.expr.is_some()),
        _ => 1,
    }
}

#[derive(Default)]
struct ControlFlowCollector {
    decision_points: usize,
    match_arms: usize,
    max_nesting_depth: usize,
    current_depth: usize,
}

impl ControlFlowCollector {
    fn record_branch(&mut self, arms: usize) {
        self.decision_points += 1;
        self.match_arms += arms;
        self.current_depth += 1;
        self.max_nesting_depth = self.max_nesting_depth.max(self.current_depth);
    }

    fn record_simple_branch(&mut self) {
        self.decision_points += 1;
        self.current_depth += 1;
        self.max_nesting_depth = self.max_nesting_depth.max(self.current_depth);
    }

    fn exit_branch(&mut self) {
        self.current_depth = self.current_depth.saturating_sub(1);
    }
}

impl<'tcx> Visitor<'tcx> for ControlFlowCollector {
    fn visit_expr(&mut self, expr: &'tcx Expr<'tcx>) {
        match expr.kind {
            ExprKind::If(..) | ExprKind::Loop(..) => {
                self.record_simple_branch();
                walk_expr(self, expr);
                self.exit_branch();
            }
            ExprKind::Match(_, arms, source) if matches!(source, MatchSource::Normal) => {
                self.record_branch(arms.len());
                walk_expr(self, expr);
                self.exit_branch();
            }
            _ => walk_expr(self, expr),
        }
    }
}

pub fn collect_boundary_function_metrics<C: HasSourceMap>(
    cx: &C,
    body: &Body<'_>,
    span: Span,
) -> Option<BoundaryFunctionMetrics> {
    let source_map = cx.source_map();
    let source = source_map.span_to_snippet(span).ok()?;
    let line_count = source.lines().count();

    let mut collector = ControlFlowCollector::default();
    collector.visit_expr(&body.value);

    let boolean_ops = crate::domain::metrics::count_boolean_operators(&source);

    Some(BoundaryFunctionMetrics {
        line_count,
        statement_count: count_statements_in_body(body),
        decision_points: collector.decision_points,
        boolean_operators: boolean_ops,
        match_arms: collector.match_arms,
        max_nesting_depth: collector.max_nesting_depth,
    })
}

/// Trait for contexts that provide access to the source map.
///
/// This allows the boundary check to work with both `EarlyContext` and
/// `LateContext` without code duplication.
pub trait HasSourceMap {
    fn source_map(&self) -> &rustc_span::source_map::SourceMap;
}

impl HasSourceMap for rustc_lint::EarlyContext<'_> {
    fn source_map(&self) -> &rustc_span::source_map::SourceMap {
        self.sess().source_map()
    }
}

impl<'tcx> HasSourceMap for rustc_lint::LateContext<'tcx> {
    fn source_map(&self) -> &rustc_span::source_map::SourceMap {
        self.sess().source_map()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::metrics::{count_boolean_operators, count_decision_points};

    fn metrics(
        line_count: usize,
        statement_count: usize,
        decision_points: usize,
        boolean_operators: usize,
        match_arms: usize,
        max_nesting_depth: usize,
    ) -> BoundaryFunctionMetrics {
        BoundaryFunctionMetrics {
            line_count,
            statement_count,
            decision_points,
            boolean_operators,
            match_arms,
            max_nesting_depth,
        }
    }

    #[test]
    fn boundary_io_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/io/writer.rs"
        )));
    }

    #[test]
    fn boundary_runtime_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/runtime/executor.rs"
        )));
    }

    #[test]
    fn boundary_ffi_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/ffi/bindings.rs"
        )));
    }

    #[test]
    fn boundary_dir_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/boundary/adapter.rs"
        )));
    }

    #[test]
    fn non_boundary_module_is_not_detected() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/pipeline/state.rs"
        )));
    }

    #[test]
    fn non_boundary_domain_module_is_not_detected() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/reducer/logic.rs"
        )));
    }

    #[test]
    fn file_level_io_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new("src/io.rs")));
    }

    #[test]
    fn file_level_runtime_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/runtime.rs"
        )));
    }

    #[test]
    fn iostream_is_not_a_boundary_module() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/iostream/reader.rs"
        )));
    }

    #[test]
    fn runtimeconfig_is_not_a_boundary_module() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/runtimeconfig/settings.rs"
        )));
    }

    #[test]
    fn simple_boundary_function_does_not_need_split() {
        assert!(!boundary_function_needs_split(&metrics(12, 4, 1, 0, 0, 1)));
    }

    #[test]
    fn long_boundary_function_with_multiple_decisions_needs_split() {
        assert!(boundary_function_needs_split(&metrics(18, 8, 2, 0, 3, 2)));
    }

    #[test]
    fn long_but_linear_boundary_function_can_stay_allowed() {
        assert!(!boundary_function_needs_split(&metrics(20, 11, 0, 0, 0, 1)));
    }

    #[test]
    fn branchy_but_tiny_boundary_function_can_stay_allowed() {
        assert!(!boundary_function_needs_split(&metrics(8, 3, 2, 0, 2, 1)));
    }

    #[test]
    fn counts_if_and_match_as_decision_points() {
        let source = "if retry { do_work(); }\nlet next = match state { A => 1, B => 2 };\n";

        assert_eq!(count_decision_points(source), 2);
    }

    #[test]
    fn complexity_score_grows_with_lines_decisions_and_statements() {
        assert_eq!(
            boundary_function_complexity_score(&metrics(18, 8, 2, 0, 2, 1)),
            6
        );
    }

    #[test]
    fn counts_boolean_operators_in_guard_expressions() {
        let source = "if retry && ready || force { run(); }\n";

        assert_eq!(count_boolean_operators(source), 2);
    }

    #[test]
    fn nested_boundary_function_needs_split_even_when_not_huge() {
        let nested = metrics(13, 9, 3, 2, 3, 3);

        assert!(boundary_function_needs_split(&nested));
    }

    #[test]
    fn wide_match_in_boundary_function_counts_toward_split_pressure() {
        let wide_match = metrics(14, 7, 1, 0, 5, 2);

        assert!(boundary_function_needs_split(&wide_match));
    }
}
