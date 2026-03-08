// Bounded execution history newtype.
//
// Enforces a bounded ring-buffer API over a VecDeque of ExecutionStep.
// Direct mutation of the inner deque is prevented; callers must use the
// bounded API (`push_bounded`, `replace_bounded`).

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

    pub(crate) fn push_bounded(&mut self, step: ExecutionStep, limit: usize) {
        self.0.push_back(step);
        while self.0.len() > limit {
            self.0.pop_front();
        }
    }

    pub(crate) fn replace_bounded(
        &mut self,
        history: std::collections::VecDeque<ExecutionStep>,
        limit: usize,
    ) {
        self.0 = history;
        while self.0.len() > limit {
            self.0.pop_front();
        }
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
