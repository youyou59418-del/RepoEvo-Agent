from __future__ import annotations

from pathlib import Path

from repoevo.context import ArtifactStore, CodeRetriever, ContextAssembler, ContextItem


def test_large_tool_output_is_artifact_backed(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    assembler = ContextAssembler(store, max_chars=2000)
    result = assembler.assemble(
        [
            ContextItem(kind="task", source="request", priority=3, content="Fix the parser."),
            ContextItem(kind="tool_output", source="pytest", priority=1, content="x" * 10000),
        ]
    )
    assert result.compressed is True
    assert len(result.artifact_refs) == 1
    assert result.artifact_refs[0].byte_size == 10000
    assert result.char_count <= 2000
    assert store.read(result.artifact_refs[0]) == b"x" * 10000


def test_high_priority_facts_survive_compression(tmp_path: Path) -> None:
    assembler = ContextAssembler(ArtifactStore(tmp_path / "artifacts"), max_chars=600)
    result = assembler.assemble(
        [
            ContextItem(kind="task", source="acceptance", priority=3, content="MUST preserve API v2."),
            ContextItem(kind="evidence", source="noise", priority=0, content="noise " * 500),
        ]
    )
    assert "MUST preserve API v2" in result.text
    assert result.compressed is True


def test_python_code_retrieval_returns_symbol_and_lines(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        "def calculate_total(items):\n    return sum(items)\n\n"
        "def unrelated(value):\n    return value\n",
        encoding="utf-8",
    )
    hits = CodeRetriever().retrieve(tmp_path, "calculate total")
    assert hits
    assert hits[0].path == "service.py"
    assert "calculate_total" in hits[0].symbols
    assert hits[0].line_start == 1
