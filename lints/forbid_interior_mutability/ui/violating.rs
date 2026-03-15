use std::sync::Mutex;

struct Holder {
    value: Mutex<i32>,
}

fn main() {
    let _ = Holder {
        value: Mutex::new(1),
    };
}
