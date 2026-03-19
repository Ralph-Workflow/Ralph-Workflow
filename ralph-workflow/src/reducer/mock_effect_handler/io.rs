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

    pub fn take_effects(&self) -> Vec<Effect> {
        self.effects.borrow_mut().drain(..).collect()
    }

    pub fn take_ui_events(&self) -> Vec<UIEvent> {
        self.ui_events.borrow_mut().drain(..).collect()
    }

    pub fn take_events(&self) -> Vec<PipelineEvent> {
        self.events.borrow_mut().drain(..).collect()
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

    pub fn was_effect_executed<F>(&self, predicate: F) -> bool
    where
        F: Fn(&Effect) -> bool,
    {
        self.effects.borrow().iter().any(predicate)
    }

    pub fn was_ui_event_emitted<F>(&self, predicate: F) -> bool
    where
        F: Fn(&UIEvent) -> bool,
    {
        self.ui_events.borrow().iter().any(predicate)
    }

    pub fn was_event_emitted<F>(&self, predicate: F) -> bool
    where
        F: Fn(&PipelineEvent) -> bool,
    {
        self.events.borrow().iter().any(predicate)
    }

    pub fn effect_count(&self) -> usize {
        self.effects.borrow().len()
    }

    pub fn ui_event_count(&self) -> usize {
        self.ui_events.borrow().len()
    }

    pub fn event_count(&self) -> usize {
        self.events.borrow().len()
    }
}
