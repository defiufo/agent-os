from __future__ import annotations

from pathlib import Path

import pytest

from agentos.memory.ingest import (
    extract_document_text,
    ingest_directory,
    ingest_document,
)
from agentos.memory.retrieval import MemoryRetriever
from agentos.memory.source_paths import is_knowledge_base_source_path, is_searchable_source_path
from agentos.memory.store import LongTermMemoryStore
from agentos.memory.sync_manager import MemorySyncManager
from agentos.memory.types import (
    MemorySource,
    normalize_memory_source_filter,
)
from agentos.tools.builtin.memory_tools import create_memory_tools
from agentos.tools.registry import ToolRegistry


def test_normalize_memory_source_filter():
    assert normalize_memory_source_filter("memory") is MemorySource.memory
    assert normalize_memory_source_filter("sessions") is MemorySource.sessions
    assert normalize_memory_source_filter("knowledge_base") is MemorySource.knowledge_base
    assert normalize_memory_source_filter("knowledge-base") is MemorySource.knowledge_base
    assert normalize_memory_source_filter("knowledge") is MemorySource.knowledge_base
    assert normalize_memory_source_filter("kb") is MemorySource.knowledge_base
    assert normalize_memory_source_filter("docs") is MemorySource.knowledge_base
    assert normalize_memory_source_filter("all", allow_all=True) is None

    with pytest.raises(ValueError, match="source must be"):
        normalize_memory_source_filter("invalid")


def test_is_knowledge_base_source_path():
    assert is_knowledge_base_source_path("knowledge_base/report.pdf")
    assert is_knowledge_base_source_path("knowledge_base/notes/2026.txt")
    assert is_knowledge_base_source_path("docs/architecture.md")
    assert not is_knowledge_base_source_path("/absolute/path.pdf")
    assert not is_knowledge_base_source_path("../parent.pdf")
    assert not is_knowledge_base_source_path("knowledge_base/.hidden.txt")

    assert is_searchable_source_path(MemorySource.knowledge_base, "knowledge_base/notes.txt")
    assert is_searchable_source_path("knowledge_base", "docs/handbook.md")


def test_extract_document_text(tmp_path: Path):
    txt_file = tmp_path / "sample.txt"
    txt_file.write_text("Hello AgentOS knowledge base!", encoding="utf-8")
    assert extract_document_text(txt_file) == "Hello AgentOS knowledge base!"

    raw_bytes = b"Direct bytes document content"
    assert extract_document_text(raw_bytes, filename="doc.txt") == "Direct bytes document content"


@pytest.mark.asyncio
async def test_ingest_document_and_directory(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    store = LongTermMemoryStore(db_path)
    await store.initialize()

    try:
        # 1. Ingest single document
        doc_file = tmp_path / "architecture.md"
        doc_file.write_text(
            "# AgentOS Architecture\n\nModular design with Pilot Router.", encoding="utf-8"
        )

        res = await ingest_document(store, doc_file, rel_path="knowledge_base/architecture.md")
        assert res.status == "indexed"
        assert res.chunks_indexed > 0
        assert res.path == "knowledge_base/architecture.md"

        # 2. Ingest directory
        docs_dir = tmp_path / "corpus"
        docs_dir.mkdir()
        (docs_dir / "file1.txt").write_text("Kubernetes cluster setup details.", encoding="utf-8")
        (docs_dir / "file2.md").write_text("PostgreSQL performance tuning.", encoding="utf-8")
        (docs_dir / ".ignored.txt").write_text("Should be skipped", encoding="utf-8")

        dir_results = await ingest_directory(
            store, docs_dir, base_rel_prefix="knowledge_base/corpus"
        )
        assert len(dir_results) == 2
        assert all(r.status == "indexed" for r in dir_results)

        # 3. Verify search returns knowledge_base source
        results, _ = await store.search(
            "Kubernetes cluster", source=MemorySource.knowledge_base, min_score=0.0
        )
        assert len(results) > 0
        assert results[0].source is MemorySource.knowledge_base
        assert "Kubernetes" in results[0].snippet

        # 4. Source counts verify knowledge_base
        counts = await store.source_counts()
        assert "knowledge_base" in counts
        assert counts["knowledge_base"]["files"] == 3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sync_manager_syncs_knowledge_base_directory(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_dir = workspace / "memory"
    memory_dir.mkdir()
    kb_dir = workspace / "knowledge_base"
    kb_dir.mkdir()

    (kb_dir / "guide.md").write_text(
        "Deployment runbook for production services.", encoding="utf-8"
    )

    db_path = tmp_path / "memory.db"
    store = LongTermMemoryStore(db_path)
    await store.initialize()

    sync_manager = MemorySyncManager(
        store=store,
        workspace_dir=workspace,
        memory_dir=memory_dir,
    )
    try:
        await sync_manager.start()

        counts = await store.source_counts()
        assert "knowledge_base" in counts
        assert counts["knowledge_base"]["files"] == 1

        results, _ = await store.search(
            "runbook", source=MemorySource.knowledge_base, min_score=0.0
        )
        assert len(results) > 0
        assert results[0].source is MemorySource.knowledge_base
    finally:
        await sync_manager.stop()
        await store.close()


@pytest.mark.asyncio
async def test_memory_tools_search_knowledge_base(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    store = LongTermMemoryStore(db_path)
    await store.initialize()

    await store.index_file(
        path="knowledge_base/policy.md",
        content="Security guidelines: all endpoints must be authenticated.",
        source=MemorySource.knowledge_base,
    )

    retriever = MemoryRetriever(store)
    registry = ToolRegistry()
    create_memory_tools(store, retriever, memory_dir=str(tmp_path), registry=registry)

    search_tool = registry.get("memory_search")
    assert search_tool is not None

    output = await search_tool.handler(
        query="Security guidelines", source="knowledge_base", max_results=5
    )
    assert "knowledge_base/policy.md" in output
    assert "authenticated" in output

    await store.close()
