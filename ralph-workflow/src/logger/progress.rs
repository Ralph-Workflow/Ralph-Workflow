//! Progress bar display utilities.
//!
//! Provides visual progress feedback for long-running operations.

use super::Colors;
use crate::logger::io::stdout_writer::stdout_write_line;

/// Print a progress bar with percentage and counts.
///
/// Displays a visual progress bar like: `[████████░░░░░░░░] 50% (5/10)`
///
/// # Arguments
///
/// * `current` - Current progress value
/// * `total` - Total value for 100% completion
/// * `label` - Label to display before the progress bar
pub fn print_progress(current: u32, total: u32, label: &str) {
    let c = Colors::new();

    if total == 0 {
        let line = format!(
            "{}{}:{} {}[no progress data]{}",
            c.dim(),
            label,
            c.reset(),
            c.yellow(),
            c.reset()
        );
        let _ = stdout_write_line(&line);
        return;
    }

    let bar_width: usize = 20;
    // Safe: result is bounded to 0..=100 by .min(100)
    let pct = u32::try_from(
        (u64::from(current))
            .saturating_mul(100)
            .saturating_div(u64::from(total))
            .min(100),
    )
    .unwrap_or(0);
    // Safe: result is bounded to 0..=bar_width by .min(bar_width)
    let filled = usize::try_from(
        (u64::from(current))
            .saturating_mul(bar_width as u64)
            .saturating_div(u64::from(total))
            .min(bar_width as u64),
    )
    .unwrap_or(0);
    let empty = bar_width - filled;

    let bar: String = format!("{}{}", "█".repeat(filled), "░".repeat(empty));

    let line = format!(
        "{}{}:{} {}[{}]{} {}{}%{} ({}/{})",
        c.dim(),
        label,
        c.reset(),
        c.cyan(),
        bar,
        c.reset(),
        c.bold(),
        pct,
        c.reset(),
        current,
        total
    );
    let _ = stdout_write_line(&line);
}

#[cfg(test)]
mod tests {
    /// Helper function for testing progress bar generation logic
    fn generate_progress_bar(current: u32, total: u32) -> (u32, String) {
        if total == 0 {
            return (0, String::new());
        }
        let bar_width: usize = 20;
        let pct = u32::try_from(
            (u64::from(current))
                .saturating_mul(100)
                .saturating_div(u64::from(total))
                .min(100),
        )
        .unwrap_or(0);
        let filled = usize::try_from(
            (u64::from(current))
                .saturating_mul(bar_width as u64)
                .saturating_div(u64::from(total))
                .min(bar_width as u64),
        )
        .unwrap_or(0);
        let empty = bar_width - filled;
        let bar: String = format!("{}{}", "█".repeat(filled), "░".repeat(empty));
        (pct, bar)
    }

    #[test]
    fn test_progress_bar_50_percent() {
        let (pct, bar) = generate_progress_bar(5, 10);
        assert_eq!(pct, 50);
        assert_eq!(bar, "██████████░░░░░░░░░░");
    }

    #[test]
    fn test_progress_bar_100_percent() {
        let (pct, bar) = generate_progress_bar(10, 10);
        assert_eq!(pct, 100);
        assert_eq!(bar, "████████████████████");
    }

    #[test]
    fn test_progress_bar_0_percent() {
        let (pct, bar) = generate_progress_bar(0, 10);
        assert_eq!(pct, 0);
        assert_eq!(bar, "░░░░░░░░░░░░░░░░░░░░");
    }

    #[test]
    fn test_progress_bar_zero_total() {
        let (pct, bar) = generate_progress_bar(0, 0);
        assert_eq!(pct, 0);
        assert_eq!(bar, "");
    }
}
