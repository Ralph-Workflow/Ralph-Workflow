use serde_json::Value;
use std::path::Path;

pub fn read_checkpoint(agent_dir: &Path) -> Option<Value> {
    let checkpoint_file = agent_dir.join("checkpoint.json");
    if !checkpoint_file.exists() {
        return None;
    }

    let content = std::fs::read_to_string(&checkpoint_file).ok()?;
    serde_json::from_str::<Value>(&content).ok()
}
