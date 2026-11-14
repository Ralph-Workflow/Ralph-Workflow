from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.format_docs import (
    FORMAT_DOC_ARTIFACT_TYPES,
    FORMAT_DOCS_INDEX_FILENAME,
    FORMAT_DOCS_WORKSPACE_DIR,
    materialize_all_format_docs,
)


def test_materialize_all_format_docs_prunes_obsolete_docs_and_examples(tmp_path: Path) -> None:
    formats_dir = tmp_path / FORMAT_DOCS_WORKSPACE_DIR
    examples_dir = formats_dir / "examples"
    examples_dir.mkdir(parents=True, exist_ok=True)

    stale_name = "commit_" + "cleanup.md"
    stale_doc = formats_dir / stale_name
    stale_doc.write_text("# obsolete", encoding="utf-8")
    stale_example = examples_dir / stale_name
    stale_example.write_text("# obsolete example", encoding="utf-8")

    kept_txt = formats_dir / "preserve.txt"
    kept_txt.write_text("keep this text file", encoding="utf-8")
    kept_example_txt = examples_dir / "preserve.txt"
    kept_example_txt.write_text("keep this example text file", encoding="utf-8")

    materialize_all_format_docs(tmp_path)

    assert not stale_doc.exists()
    assert not stale_example.exists()
    assert kept_txt.exists()
    assert kept_example_txt.exists()

    index_path = formats_dir / FORMAT_DOCS_INDEX_FILENAME
    assert index_path.exists()

    for artifact_type in FORMAT_DOC_ARTIFACT_TYPES:
        assert (formats_dir / f"{artifact_type}.md").exists()
        assert (examples_dir / f"{artifact_type}.md").exists()


def test_materialize_all_format_docs_idempotent_with_no_obsolete_files(tmp_path: Path) -> None:
    materialize_all_format_docs(tmp_path)
    formats_dir = tmp_path / FORMAT_DOCS_WORKSPACE_DIR
    examples_dir = formats_dir / "examples"

    format_files_before = sorted(p.name for p in formats_dir.glob("*.md"))
    example_files_before = sorted(p.name for p in examples_dir.glob("*.md"))

    materialize_all_format_docs(tmp_path)

    format_files_after = sorted(p.name for p in formats_dir.glob("*.md"))
    example_files_after = sorted(p.name for p in examples_dir.glob("*.md"))

    assert format_files_before == format_files_after
    assert example_files_before == example_files_after
