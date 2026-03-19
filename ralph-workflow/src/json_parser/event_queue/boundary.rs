use mpsc::{Receiver, SyncSender};

pub fn drain_queue<T>(receiver: &mut Receiver<T>) -> Vec<T> {
    let mut drained = Vec::new();
    while let Ok(event) = receiver.try_recv() {
        drained.push(event);
    }
    drained
}

pub fn send_with_backpressure<T>(
    sender: &SyncSender<T>,
    event: T,
) -> Result<(), mpsc::SendError<T>> {
    sender.send(event)
}

pub fn try_send_nonblocking<T>(
    sender: &SyncSender<T>,
    event: T,
) -> Result<(), mpsc::TrySendError<T>> {
    sender.try_send(event)
}

#[cfg(test)]
pub fn clear_via_receiver<T>(receiver: &mut Receiver<T>) {
    while receiver.try_recv().is_ok() {}
}
