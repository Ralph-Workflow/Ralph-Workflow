// Test: forbid_mut_binding should allow mut self in builder pattern
#![allow(dead_code)]

pub struct Builder {
    field: i32,
}

impl Builder {
    // This should NOT trigger the lint - consuming builder pattern
    pub fn with_field(mut self, field: i32) -> Self {
        self.field = field;
        self
    }

    // This should NOT trigger the lint - consuming builder pattern
    pub fn with_increment(mut self) -> Self {
        self.field += 1;
        self
    }
}

pub fn example_usage() {
    let builder = Builder { field: 0 };
    let builder = builder.with_field(42).with_increment();
    drop(builder);
}

fn main() {}
