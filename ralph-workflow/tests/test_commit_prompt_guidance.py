from ralph.prompts.commit import prompt_commit_message, prompt_commit_message_for_opencode
from ralph.prompts.commit_evidence import CommitEvidenceBundle


def test_commit_prompts_place_evidence_guidance_before_document_grammar() -> None:
    evidence = CommitEvidenceBundle(
        "diff --git a/ralph/api.py b/ralph/api.py\n+preserve retry status\n",
        ("ralph/api.py",),
        ("ralph",),
        (),
        ("ralph/api.py",),
        verification_facts=("pytest tests/test_api.py -q (passed)",),
        behavior_facts=("preserve retry status",),
    )

    prompts = (
        prompt_commit_message(evidence),
        prompt_commit_message_for_opencode(evidence, submit_artifact_tool_name="ralph_submit_md_artifact"),
    )

    for prompt, heading in zip(
        prompts, ("WRITE THE MESSAGE FIRST", "Write the message first"), strict=True
    ):
        assert prompt.index(heading) < prompt.index("Document shape")
        assert "pytest tests/test_api.py -q (passed)" in prompt
        assert "Small:" in prompt and "Medium:" in prompt and "Large:" in prompt
