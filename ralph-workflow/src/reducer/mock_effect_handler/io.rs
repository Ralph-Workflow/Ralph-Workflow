use std::cell::RefCell;

use super::{Effect, PipelineEvent, UIEvent};

#[derive(Debug)]
pub struct CapturedState {
    pub effects: RefCell<Vec<Effect>>,
    pub ui_events: RefCell<Vec<UIEvent>>,
    pub events: RefCell<Vec<PipelineEvent>>,
}

impl Default for CapturedState {
    fn default() -> Self {
        Self {
            effects: RefCell::new(Vec::new()),
            ui_events: RefCell::new(Vec::new()),
            events: RefCell::new(Vec::new()),
        }
    }
}

impl CapturedState {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn push_effect(&self, effect: Effect) {
        self.effects.borrow_mut().push(effect);
    }

    pub fn push_ui_event(&self, event: UIEvent) {
        self.ui_events.borrow_mut().push(event);
    }

    pub fn push_event(&self, event: PipelineEvent) {
        self.events.borrow_mut().push(event);
    }
}
