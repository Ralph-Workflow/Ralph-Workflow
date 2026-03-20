use crate::json_parser::deduplication::DeltaDeduplicator;
use crate::json_parser::types::ContentType;
use itertools::Itertools;
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};

pub(super) fn merge_delta(
    accumulated: &mut HashMap<(ContentType, String), String>,
    key_order: &mut Vec<(ContentType, String)>,
    output_started_for_key: &mut HashSet<(ContentType, String)>,
    content_type: ContentType,
    key: &str,
    delta: &str,
) -> bool {
    let content_key = (content_type, key.to_string());
    let is_first = !output_started_for_key.contains(&content_key);

    output_started_for_key.insert(content_key.clone());

    accumulated
        .entry(content_key.clone())
        .and_modify(|buf| buf.push_str(delta))
        .or_insert_with(|| delta.to_string());

    if is_first {
        key_order.push(content_key);
    }

    is_first
}

pub(super) fn sorted_content_keys(
    accumulated: &HashMap<(ContentType, String), String>,
    content_type: ContentType,
) -> Vec<String> {
    accumulated
        .keys()
        .filter(|(ty, _key)| *ty == content_type)
        .map(|(_ty, key)| key.clone())
        .sorted_by(|a, b| {
            let a_num = a.parse::<u64>();
            let b_num = b.parse::<u64>();
            match (a_num, b_num) {
                (Ok(a), Ok(b)) => a.cmp(&b),
                _ => a.cmp(b),
            }
        })
        .unique()
        .collect()
}

pub(super) fn compute_hash(value: &str) -> u64 {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    value.hash(&mut hasher);
    hasher.finish()
}

pub(super) fn compute_content_hash_from_accumulated(
    accumulated: &HashMap<(ContentType, String), String>,
) -> Option<u64> {
    if accumulated.is_empty() {
        return None;
    }

    let hash = accumulated
        .keys()
        .sorted_by_key(|k| {
            let type_order = match k.0 {
                ContentType::Text => 0,
                ContentType::ToolInput => 1,
                ContentType::Thinking => 2,
            };
            let index = k.1.parse::<u64>().unwrap_or(u64::MAX);
            (index, type_order)
        })
        .fold(0u64, |acc, key| {
            let content = accumulated.get(key);
            let key_hash = format!("{:?}-{}", key.0, key.1);
            let key_hash_bytes = key_hash.as_bytes();
            let content_bytes = content.map(|s| s.as_bytes()).unwrap_or(b"");
            acc.wrapping_add(crate::json_parser::boundary::compute_hash(&[
                key_hash_bytes,
                content_bytes,
            ]))
        });

    Some(hash)
}

pub(super) fn is_duplicate_text_content(
    accumulated: &HashMap<(ContentType, String), String>,
    content: &str,
) -> bool {
    let combined_content: String = accumulated
        .keys()
        .filter(|(ct, _)| *ct == ContentType::Text)
        .sorted_by_key(|k| k.1.parse::<u64>().unwrap_or(u64::MAX))
        .filter_map(|key| accumulated.get(key))
        .cloned()
        .collect();

    combined_content == content
}

pub(super) fn build_tool_use_reconstruction(
    accumulated: &HashMap<(ContentType, String), String>,
    tool_names: &HashMap<u64, Option<String>>,
    tool_name_hints: Option<&HashMap<usize, String>>,
) -> String {
    accumulated
        .keys()
        .filter(|(ct, _)| *ct == ContentType::ToolInput)
        .sorted_by_key(|k| k.1.parse::<u64>().unwrap_or(u64::MAX))
        .filter_map(|(ct, index_str)| {
            let accumulated_input = accumulated.get(&(*ct, index_str.clone()))?;
            let index_num = index_str.parse::<u64>().unwrap_or(0);
            let tool_name = usize::try_from(index_num)
                .ok()
                .and_then(|idx| {
                    tool_name_hints
                        .and_then(|hints| hints.get(&idx).map(std::string::String::as_str))
                })
                .or_else(|| tool_names.get(&index_num).and_then(|n| n.as_deref()))
                .unwrap_or("");
            Some(format!("TOOL_USE:{tool_name}:{accumulated_input}"))
        })
        .collect()
}

pub(super) fn build_mixed_content_reconstruction(
    accumulated: &HashMap<(ContentType, String), String>,
    tool_names: &HashMap<u64, Option<String>>,
    tool_name_hints: Option<&HashMap<usize, String>>,
) -> String {
    accumulated
        .keys()
        .sorted_by_key(|k| {
            let index = k.1.parse::<u64>().unwrap_or(u64::MAX);
            let type_order = match k.0 {
                ContentType::Text => 0,
                ContentType::ToolInput => 1,
                ContentType::Thinking => 2,
            };
            (index, type_order)
        })
        .filter_map(|(ct, index_str)| {
            let accumulated_content = accumulated.get(&(*ct, index_str.clone()))?;
            match ct {
                ContentType::Text => Some(accumulated_content.clone()),
                ContentType::ToolInput => {
                    let index_num = index_str.parse::<u64>().unwrap_or(0);
                    let tool_name = usize::try_from(index_num)
                        .ok()
                        .and_then(|idx| {
                            tool_name_hints
                                .and_then(|hints| hints.get(&idx).map(std::string::String::as_str))
                        })
                        .or_else(|| tool_names.get(&index_num).and_then(|n| n.as_deref()))
                        .unwrap_or("");
                    Some(format!("TOOL_USE:{tool_name}:{accumulated_content}"))
                }
                ContentType::Thinking => None,
            }
        })
        .collect()
}

pub(super) fn is_likely_snapshot(
    accumulated: &HashMap<(ContentType, String), String>,
    text: &str,
    key: &str,
) -> bool {
    let content_key = (ContentType::Text, key.to_string());
    accumulated.get(&content_key).is_some_and(|previous| {
        DeltaDeduplicator::is_likely_snapshot_with_thresholds(text, previous)
    })
}

pub(super) fn extract_delta_from_snapshot(
    accumulated: &HashMap<(ContentType, String), String>,
    text: &str,
    key: &str,
) -> Result<usize, String> {
    let content_key = (ContentType::Text, key.to_string());

    if let Some(previous) = accumulated.get(&content_key) {
        if let Some(new_content) =
            DeltaDeduplicator::extract_new_content_with_thresholds(text, previous)
        {
            let delta_start = text.len() - new_content.len();
            return Ok(delta_start);
        }
    }

    Err(format!(
        "extract_delta_from_snapshot called on non-snapshot text. key={key:?}, text={text:?}. Snapshot detection may have had a false positive."
    ))
}
