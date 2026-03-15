struct Counter;

impl Counter {
    fn increment(&mut self) {}
}

fn main() {
    let mut counter = Counter;
    counter.increment();
}
