// Bounded execution history newtype.
//
// Enforces a bounded ring-buffer API over a VecDeque of ExecutionStep.
// Direct mutation of the inner deque is prevented; callers must use the
// functional API (`with_step`, `with_replaced`).

/// Execution step history with bounded insertion.
///
/// This newtype enforces that callers cannot mutate the underlying `VecDeque` directly
/// (e.g., via `push_back`) and must instead use a bounded API.
#[derive(Clone, Serialize, Deserialize, Debug, Default)]
#[serde(transparent)]
pub struct BoundedExecutionHistory(std::collections::VecDeque<ExecutionStep>);

impl BoundedExecutionHistory {
    #[must_use]
    pub const fn new() -> Self {
        Self(std::collections::VecDeque::new())
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.0.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    #[must_use]
    pub fn iter(&self) -> std::collections::vec_deque::Iter<'_, ExecutionStep> {
        self.0.iter()
    }

    #[must_use]
    pub const fn as_deque(&self) -> &std::collections::VecDeque<ExecutionStep> {
        &self.0
    }

    /// Add a step to history with automatic bounding, returning a new instance.
    ///
    /// This method implements a ring buffer strategy: when the history exceeds
    /// the configured limit, the oldest entries are dropped to maintain a bounded
    /// memory footprint.
    #[must_use]
    pub fn with_step(self, step: ExecutionStep, limit: usize) -> Self {
        // Chain existing steps with new step, then skip excess from front to maintain limit
        let current_len = self.0.len();
        let excess = current_len.saturating_add(1).saturating_sub(limit);
        let new_deque: std::collections::VecDeque<_> = self
            .0
            .into_iter()
            .chain(std::iter::once(step))
            .skip(excess)
            .collect();
        Self(new_deque)
    }

    /// Replace the entire history with bounding, returning a new instance.
    #[must_use]
    pub fn with_replaced(self, history: std::collections::VecDeque<ExecutionStep>, limit: usize) -> Self {
        let excess = history.len().saturating_sub(limit);
        Self(history.into_iter().skip(excess).collect())
    }
}

impl std::ops::Deref for BoundedExecutionHistory {
    type Target = std::collections::VecDeque<ExecutionStep>;

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl<'a> IntoIterator for &'a BoundedExecutionHistory {
    type Item = &'a ExecutionStep;
    type IntoIter = std::collections::vec_deque::Iter<'a, ExecutionStep>;

    fn into_iter(self) -> Self::IntoIter {
        self.0.iter()
    }
}
