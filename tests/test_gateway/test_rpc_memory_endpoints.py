from __future__ import annotations

from pathlib import Path

import pytest

from agentos.gateway.rpc import RpcContext, get_dispatcher
from agentos.memory.manager import MemoryManager
from agentos.memory.retrieval import MemoryRetriever
from agentos.memory.store import LongTermMemoryStore
from agentos.memory.sync_manager import MemorySyncManager
from agentos.memory.turn_capture import TurnCaptureService


@pytest.mark.asyncio
async def test_rpc_memory_curated_and_knowledge_base(tmp_path: Path):
    dispatcher = get_dispatcher()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_dir = workspace / "memory"
    memory_dir.mkdir()
    db_path = tmp_path / "memory.db"

    # Setup initial curated files
    (workspace / "MEMORY.md").write_text(
        "First memory entry\n§\nSecond memory entry\n", encoding="utf-8"
    )
    (workspace / "USER.md").write_text("Prefers concise answers\n", encoding="utf-8")

    store = LongTermMemoryStore(db_path)
    await store.initialize()

    sync_manager = MemorySyncManager(store=store, workspace_dir=workspace, memory_dir=memory_dir)
    retriever = MemoryRetriever(store)
    turn_capture = TurnCaptureService(workspace_dir=workspace, turns_dir=tmp_path / "turns")

    manager = MemoryManager(
        agent_id="main",
        db_path=db_path,
        store=store,
        sync_manager=sync_manager,
        retriever=retriever,
        turn_capture=turn_capture,
        workspace_dir=workspace,
        memory_dir=memory_dir,
    )

    ctx = RpcContext(conn_id="test")
    ctx.memory_managers = {"main": manager}

    try:
        # 1. memory.curated.get
        res = await dispatcher.dispatch(
            "r1", "memory.curated.get", {"agentId": "main", "target": "memory"}, ctx
        )
        assert res.ok
        assert res.payload["target"] == "memory"
        assert len(res.payload["entries"]) == 2
        assert "First memory entry" in res.payload["entries"]

        # 2. memory.curated.add
        add_res = await dispatcher.dispatch(
            "r2",
            "memory.curated.add",
            {"agentId": "main", "target": "memory", "content": "Third memory entry"},
            ctx,
        )
        assert add_res.ok
        assert len(add_res.payload["entries"]) == 3
        assert "Third memory entry" in add_res.payload["entries"]

        # 3. memory.curated.replace
        replace_res = await dispatcher.dispatch(
            "r3",
            "memory.curated.replace",
            {
                "agentId": "main",
                "target": "memory",
                "oldText": "Second memory entry",
                "newContent": "Updated second entry",
            },
            ctx,
        )
        assert replace_res.ok
        assert "Updated second entry" in replace_res.payload["entries"]
        assert "Second memory entry" not in replace_res.payload["entries"]

        # 4. memory.curated.remove
        remove_res = await dispatcher.dispatch(
            "r4",
            "memory.curated.remove",
            {"agentId": "main", "target": "memory", "oldText": "Updated second entry"},
            ctx,
        )
        assert remove_res.ok
        assert "Updated second entry" not in remove_res.payload["entries"]

        # 5. memory.curated.batch
        batch_res = await dispatcher.dispatch(
            "r5",
            "memory.curated.batch",
            {
                "agentId": "main",
                "target": "user",
                "operations": [
                    {"action": "add", "content": "Uses metric system"},
                    {"action": "add", "content": "Dark mode preferred"},
                ],
            },
            ctx,
        )
        assert batch_res.ok
        assert len(batch_res.payload["entries"]) == 3
        assert "Uses metric system" in batch_res.payload["entries"]

        # 6. memory.knowledge_base.ingest (direct content)
        ingest_res = await dispatcher.dispatch(
            "r6",
            "memory.knowledge_base.ingest",
            {
                "agentId": "main",
                "content": "Kubernetes cluster configuration handbook.",
                "filename": "k8s.txt",
            },
            ctx,
        )
        assert ingest_res.ok
        assert len(ingest_res.payload["results"]) == 1
        assert ingest_res.payload["results"][0]["status"] == "indexed"
        assert ingest_res.payload["results"][0]["path"] == "knowledge_base/k8s.txt"

        # 7. memory.knowledge_base.list
        kb_list = await dispatcher.dispatch(
            "r7", "memory.knowledge_base.list", {"agentId": "main"}, ctx
        )
        assert kb_list.ok
        assert kb_list.payload["count"] == 1
        assert kb_list.payload["documents"][0]["path"] == "knowledge_base/k8s.txt"

        # 8. memory.list with source filter
        mem_list_kb = await dispatcher.dispatch(
            "r8", "memory.list", {"agentId": "main", "source": "knowledge_base"}, ctx
        )
        assert mem_list_kb.ok
        assert mem_list_kb.payload["count"] == 1
        assert mem_list_kb.payload["files"][0]["source"] == "knowledge_base"

        # 9. memory.show on knowledge base file
        show_res = await dispatcher.dispatch(
            "r9", "memory.show", {"agentId": "main", "path": "knowledge_base/k8s.txt"}, ctx
        )
        assert show_res.ok
        assert "Kubernetes" in show_res.payload["content"]

        # 10. memory.knowledge_base.remove
        rm_kb = await dispatcher.dispatch(
            "r10",
            "memory.knowledge_base.remove",
            {"agentId": "main", "path": "knowledge_base/k8s.txt"},
            ctx,
        )
        assert rm_kb.ok
        assert rm_kb.payload["removed"] is True

        kb_list_after = await dispatcher.dispatch(
            "r11", "memory.knowledge_base.list", {"agentId": "main"}, ctx
        )
        assert kb_list_after.ok
        assert kb_list_after.payload["count"] == 0

        # 11. Security boundaries: path traversal / outside ingestion rejected
        outside_file = tmp_path / "outside.txt"
        outside_file.write_text("Secret outside content", encoding="utf-8")
        ingest_outside_abs = await dispatcher.dispatch(
            "r12",
            "memory.knowledge_base.ingest",
            {"agentId": "main", "path": str(outside_file)},
            ctx,
        )
        assert not ingest_outside_abs.ok
        assert "traversal" in str(ingest_outside_abs.error).lower()

        ingest_outside_rel = await dispatcher.dispatch(
            "r13",
            "memory.knowledge_base.ingest",
            {"agentId": "main", "path": "../outside.txt"},
            ctx,
        )
        assert not ingest_outside_rel.ok
        assert "traversal" in str(ingest_outside_rel.error).lower()

        # 12. Security boundaries: cannot remove files outside knowledge_base/**
        assert (workspace / "MEMORY.md").is_file()
        rm_memory_md = await dispatcher.dispatch(
            "r14",
            "memory.knowledge_base.remove",
            {"agentId": "main", "path": "MEMORY.md"},
            ctx,
        )
        assert not rm_memory_md.ok
        assert (workspace / "MEMORY.md").is_file()

        rm_traversal = await dispatcher.dispatch(
            "r15",
            "memory.knowledge_base.remove",
            {"agentId": "main", "path": "knowledge_base/../MEMORY.md"},
            ctx,
        )
        assert not rm_traversal.ok
        assert (workspace / "MEMORY.md").is_file()

        # 13. Public char_limit and char_count accessors on CuratedMemoryStore
        curated_store = manager.curated_store()
        assert curated_store.char_limit("memory") > 0
        assert curated_store.char_count("memory") > 0

    finally:
        await store.close()
