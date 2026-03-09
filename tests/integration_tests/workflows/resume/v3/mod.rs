//! V3 hardened resume tests (execution history, file system state, prompt replay).
//!
//! These tests use `MockAppEffectHandler` for in-memory testing without
//! real filesystem or git operations.

mod execution_history;
mod file_system_state;
mod prompt_history;
mod rebase_conflict_replay;
mod smoke;

use super::STANDARD_PROMPT_CHECKSUM;

fn make_checkpoint_json_with_resume_count(working_dir: &str) -> String {
    format!(
        r#"{{
            "version": 3,
            "phase": "Complete",
            "iteration": 2,
            "total_iterations": 2,
            "reviewer_pass": 1,
            "total_reviewer_passes": 1,
            "timestamp": "2024-01-01 12:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex",
            "cli_args": {{
                "developer_iters": 0,
                "reviewer_reviews": 0,
                "commit_msg": "feat: add feature",
                "review_depth": "standard"
            }},
            "developer_agent_config": {{
                "name": "claude",
                "cmd": "claude",
                "output_flag": "",
                "yolo_flag": null,
                "can_commit": false,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "reviewer_agent_config": {{
                "name": "codex",
                "cmd": "codex",
                "output_flag": "",
                "yolo_flag": null,
                "can_commit": false,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "rebase_state": "NotStarted",
            "config_path": null,
            "config_checksum": null,
            "working_dir": "{working_dir}",
            "prompt_md_checksum": "{STANDARD_PROMPT_CHECKSUM}",
            "git_user_name": null,
            "git_user_email": null,
            "run_id": "test-run-id-456",
            "parent_run_id": "test-parent-run-id",
            "resume_count": 2,
            "actual_developer_runs": 2,
            "actual_reviewer_runs": 1,
            "execution_history": null,
            "file_system_state": null,
            "prompt_history": null
        }}"#
    )
}

fn make_comprehensive_v3_checkpoint(
    working_dir: &str,
    prompt_checksum: &str,
    plan_checksum: &str,
    prompt_len: usize,
    plan_len: usize,
) -> String {
    format!(
        r#"{{
            "version": 3,
            "phase": "Complete",
            "iteration": 1,
            "total_iterations": 1,
            "reviewer_pass": 1,
            "total_reviewer_passes": 1,
            "timestamp": "2024-01-01 12:01:00",
            "developer_agent": "claude",
            "reviewer_agent": "claude",
            "cli_args": {{
                "developer_iters": 0,
                "reviewer_reviews": 0,
                "commit_msg": "feat: add feature X",
                "review_depth": "standard"
            }},
            "developer_agent_config": {{
                "name": "claude",
                "cmd": "claude -p",
                "output_flag": "--output-format=stream-json",
                "yolo_flag": "--dangerously-skip-permissions",
                "can_commit": true,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "reviewer_agent_config": {{
                "name": "claude",
                "cmd": "claude -p",
                "output_flag": "--output-format=stream-json",
                "yolo_flag": "--dangerously-skip-permissions",
                "can_commit": true,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "rebase_state": "NotStarted",
            "config_path": null,
            "config_checksum": null,
            "working_dir": "{working_dir}",
            "prompt_md_checksum": "{prompt_checksum}",
            "git_user_name": null,
            "git_user_email": null,
            "run_id": "comprehensive-test-run-id",
            "parent_run_id": null,
            "resume_count": 0,
            "actual_developer_runs": 1,
            "actual_reviewer_runs": 1,
            "execution_history": {{
                "steps": [],
                "file_snapshots": {{}}
            }},
            "file_system_state": {{
                "files": {{
                    "PROMPT.md": {{
                        "path": "PROMPT.md",
                        "checksum": "{prompt_checksum}",
                        "size": {prompt_len},
                        "content": null,
                        "exists": true
                    }},
                    ".agent/PLAN.md": {{
                        "path": ".agent/PLAN.md",
                        "checksum": "{plan_checksum}",
                        "size": {plan_len},
                        "content": null,
                        "exists": true
                    }}
                }},
                "git_head_oid": null,
                "git_branch": null
            }},
            "prompt_history": {{
                "planning_1": "Planning prompt for iteration 1",
                "development_1": "Development prompt for iteration 1"
            }}
        }}"#
    )
}

fn make_checkpoint_with_git_commit_oid(working_dir: &str) -> String {
    format!(
        r#"{{
            "version": 3,
            "phase": "Complete",
            "iteration": 1,
            "total_iterations": 1,
            "reviewer_pass": 0,
            "total_reviewer_passes": 0,
            "timestamp": "2024-01-01 12:00:00",
            "developer_agent": "test-agent",
            "reviewer_agent": "test-agent",
            "cli_args": {{
                "developer_iters": 0,
                "reviewer_reviews": 0,
                "commit_msg": "",
                "review_depth": null
            }},
            "developer_agent_config": {{
                "name": "test-agent",
                "cmd": "echo",
                "output_flag": "",
                "yolo_flag": null,
                "can_commit": false,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "reviewer_agent_config": {{
                "name": "test-agent",
                "cmd": "echo",
                "output_flag": "",
                "yolo_flag": null,
                "can_commit": false,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "rebase_state": "NotStarted",
            "config_path": null,
            "config_checksum": null,
            "working_dir": "{working_dir}",
            "prompt_md_checksum": "{STANDARD_PROMPT_CHECKSUM}",
            "git_user_name": null,
            "git_user_email": null,
            "run_id": "test-git-commit-oid",
            "parent_run_id": null,
            "resume_count": 0,
            "actual_developer_runs": 1,
            "actual_reviewer_runs": 0,
            "execution_history": {{
                "steps": [
                    {{
                        "phase": "Development",
                        "iteration": 1,
                        "step_type": "dev_run",
                        "timestamp": "2024-01-01 12:00:00",
                        "outcome": {{
                            "Success": {{
                                "output": "Implementation complete",
                                "files_modified": ["src/lib.rs"],
                                "exit_code": 0
                            }}
                        }},
                        "agent": "test-agent",
                        "duration_secs": 120,
                        "checkpoint_saved_at": null,
                        "git_commit_oid": "abc123def456",
                        "modified_files_detail": {{
                            "added": ["src/new.rs"],
                            "modified": ["src/lib.rs"],
                            "deleted": []
                        }},
                        "prompt_used": "Implement feature X",
                        "issues_summary": {{
                            "found": 0,
                            "fixed": 0,
                            "description": null
                        }}
                    }}
                ],
                "file_snapshots": {{}}
            }},
            "file_system_state": null,
            "prompt_history": {{
                "development_1": "Implement feature X"
            }}
        }}"#
    )
}

fn make_checkpoint_with_all_new_fields(working_dir: &str) -> String {
    format!(
        r#"{{
            "version": 3,
            "phase": "Complete",
            "iteration": 1,
            "total_iterations": 1,
            "reviewer_pass": 0,
            "total_reviewer_passes": 0,
            "timestamp": "2024-01-01 12:00:00",
            "developer_agent": "test-agent",
            "reviewer_agent": "test-agent",
            "cli_args": {{
                "developer_iters": 0,
                "reviewer_reviews": 0,
                "commit_msg": "",
                "review_depth": null
            }},
            "developer_agent_config": {{
                "name": "test-agent",
                "cmd": "echo",
                "output_flag": "",
                "yolo_flag": null,
                "can_commit": false,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "reviewer_agent_config": {{
                "name": "test-agent",
                "cmd": "echo",
                "output_flag": "",
                "yolo_flag": null,
                "can_commit": false,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "rebase_state": "NotStarted",
            "config_path": null,
            "config_checksum": null,
            "working_dir": "{working_dir}",
            "prompt_md_checksum": "{STANDARD_PROMPT_CHECKSUM}",
            "git_user_name": null,
            "git_user_email": null,
            "run_id": "test-new-fields",
            "parent_run_id": null,
            "resume_count": 0,
            "actual_developer_runs": 1,
            "actual_reviewer_runs": 0,
            "execution_history": {{
                "steps": [
                    {{
                        "phase": "Development",
                        "iteration": 1,
                        "step_type": "dev_run",
                        "timestamp": "2024-01-01 12:00:00",
                        "outcome": {{
                            "Success": {{
                                "output": "Implementation complete",
                                "files_modified": [],
                                "exit_code": 0
                            }}
                        }},
                        "agent": "test-agent",
                        "duration_secs": 60,
                        "checkpoint_saved_at": null,
                        "git_commit_oid": null,
                        "modified_files_detail": null,
                        "prompt_used": "Implement the feature",
                        "issues_summary": {{
                            "found": 3,
                            "fixed": 0,
                            "description": "3 clippy warnings found"
                        }}
                    }}
                ],
                "file_snapshots": {{}}
            }},
            "file_system_state": null,
            "prompt_history": {{
                "development_1": "Implement the feature"
            }}
        }}"#
    )
}

fn make_checkpoint_without_new_fields(working_dir: &str) -> String {
    format!(
        r#"{{
            "version": 3,
            "phase": "Complete",
            "iteration": 1,
            "total_iterations": 1,
            "reviewer_pass": 0,
            "total_reviewer_passes": 0,
            "timestamp": "2024-01-01 12:00:00",
            "developer_agent": "test-agent",
            "reviewer_agent": "test-agent",
            "cli_args": {{
                "developer_iters": 0,
                "reviewer_reviews": 0,
                "commit_msg": "",
                "review_depth": null
            }},
            "developer_agent_config": {{
                "name": "test-agent",
                "cmd": "echo",
                "output_flag": "",
                "yolo_flag": null,
                "can_commit": false,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "reviewer_agent_config": {{
                "name": "test-agent",
                "cmd": "echo",
                "output_flag": "",
                "yolo_flag": null,
                "can_commit": false,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "rebase_state": "NotStarted",
            "config_path": null,
            "config_checksum": null,
            "working_dir": "{working_dir}",
            "prompt_md_checksum": null,
            "git_user_name": null,
            "git_user_email": null,
            "run_id": "test-backward-compat",
            "parent_run_id": null,
            "resume_count": 0,
            "actual_developer_runs": 1,
            "actual_reviewer_runs": 0,
            "execution_history": {{
                "steps": [
                    {{
                        "phase": "Development",
                        "iteration": 1,
                        "step_type": "dev_run",
                        "timestamp": "2024-01-01 12:00:00",
                        "outcome": {{
                            "Success": {{
                                "output": "Implementation complete",
                                "files_modified": ["src/lib.rs"],
                                "exit_code": 0
                            }}
                        }},
                        "agent": "test-agent",
                        "duration_secs": 120,
                        "checkpoint_saved_at": null
                    }}
                ],
                "file_snapshots": {{}}
            }},
            "file_system_state": null,
            "prompt_history": null
        }}"#
    )
}

fn make_checkpoint_with_detailed_execution_history(working_dir: &str) -> String {
    format!(
        r#"{{
            "version": 3,
            "phase": "Complete",
            "iteration": 1,
            "total_iterations": 1,
            "reviewer_pass": 0,
            "total_reviewer_passes": 0,
            "timestamp": "2024-01-01 12:00:00",
            "developer_agent": "test-agent",
            "reviewer_agent": "test-agent",
            "cli_args": {{
                "developer_iters": 0,
                "reviewer_reviews": 0,
                "commit_msg": "",
                "review_depth": null
            }},
            "developer_agent_config": {{
                "name": "test-agent",
                "cmd": "echo",
                "output_flag": "",
                "yolo_flag": null,
                "can_commit": false,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "reviewer_agent_config": {{
                "name": "test-agent",
                "cmd": "echo",
                "output_flag": "",
                "yolo_flag": null,
                "can_commit": false,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "rebase_state": "NotStarted",
            "config_path": null,
            "config_checksum": null,
            "working_dir": "{working_dir}",
            "prompt_md_checksum": "{STANDARD_PROMPT_CHECKSUM}",
            "git_user_name": null,
            "git_user_email": null,
            "run_id": "test-resume-note",
            "parent_run_id": null,
            "resume_count": 0,
            "actual_developer_runs": 1,
            "actual_reviewer_runs": 0,
            "execution_history": {{
                "steps": [
                    {{
                        "phase": "Development",
                        "iteration": 1,
                        "step_type": "dev_run",
                        "timestamp": "2024-01-01 12:00:00",
                        "outcome": {{
                            "Success": {{
                                "output": "Implementation complete",
                                "files_modified": ["src/lib.rs", "src/main.rs"],
                                "exit_code": 0
                            }}
                        }},
                        "agent": "test-agent",
                        "duration_secs": 120,
                        "checkpoint_saved_at": null,
                        "git_commit_oid": "abc123",
                        "modified_files_detail": {{
                            "added": ["src/new.rs"],
                            "modified": ["src/lib.rs"],
                            "deleted": ["src/old.rs"]
                        }},
                        "prompt_used": "Implement feature X",
                        "issues_summary": {{
                            "found": 5,
                            "fixed": 3,
                            "description": "3 clippy warnings fixed"
                        }}
                    }}
                ],
                "file_snapshots": {{}}
            }},
            "file_system_state": null,
            "prompt_history": {{
                "development_1": "Implement feature X"
            }}
        }}"#
    )
}
