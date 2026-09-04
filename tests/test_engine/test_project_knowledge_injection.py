"""Tests for project-knowledge injection into the turn prompt context."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio

from agentos.engine.runtime import TurnRunner
from agentos.session.manager import SessionManager
from agentos.session.storage import SessionStorage

SESSION_KEY = "agent:main:webchat:beef0001"


@pytest_asyncio.fixture
async def manager():
    storage = SessionStorage(":memory:")
    await storage.connect()
    mgr = SessionManager(storage, inject_time_prefix=False)
    yield mgr
    await storage.close()


@pytest.fixture
def runner(manager) -> TurnRunner:
    return TurnRunner(
        provider_selector=None,
        config=SimpleNamespace(
            workspace_dir=".",
            memory=SimpleNamespace(source="workspace"),
            tools=SimpleNamespace(profile=None),
        ),
        session_manager=manager,
    )


@pytest.mark.asyncio
async def test_project_session_gets_wrapped_knowledge_block(runner, manager):
    project = await manager.create_project("main", "Research", knowledge="Alpha beta facts.")
    await manager.create(SESSION_KEY, agent_id="main", project_id=project["project_id"])

    merged = await runner._augment_extra_context_with_project_knowledge(
        session_key=SESSION_KEY, extra_context={"Existing": "kept"}
    )
    assert merged is not None
    assert merged["Existing"] == "kept"
    block = merged["Project Knowledge"]
    assert "Alpha beta facts." in block
    # User-authored text rides inside the untrusted wrapper, like workspace files.
    assert "untrusted" in block


@pytest.mark.asyncio
async def test_sessions_without_project_are_untouched(runner, manager):
    await manager.create(SESSION_KEY, agent_id="main")
    context = {"Existing": "kept"}
    merged = await runner._augment_extra_context_with_project_knowledge(
        session_key=SESSION_KEY, extra_context=context
    )
    assert merged is context  # same object, nothing injected


@pytest.mark.asyncio
async def test_knowledge_edit_is_picked_up_next_turn(runner, manager):
    project = await manager.create_project("main", "Research", knowledge="v1")
    await manager.create(SESSION_KEY, agent_id="main", project_id=project["project_id"])

    first = await runner._augment_extra_context_with_project_knowledge(
        session_key=SESSION_KEY, extra_context=None
    )
    assert "v1" in first["Project Knowledge"]

    await manager.update_project(project["project_id"], knowledge="v2")
    second = await runner._augment_extra_context_with_project_knowledge(
        session_key=SESSION_KEY, extra_context=None
    )
    assert "v2" in second["Project Knowledge"]
    assert "v1" not in second["Project Knowledge"]


@pytest.mark.asyncio
async def test_oversized_knowledge_is_truncated_with_marker(runner, manager):
    """Rows written under the old 32k cap (or edited out-of-band) still get
    truncated at injection; writes above the cap are rejected up front now,
    so the legacy row is planted directly in storage."""
    from agentos.session.models import ProjectNode

    cap = TurnRunner.PROJECT_KNOWLEDGE_INJECT_MAX_CHARS
    project = await manager.create_project("main", "Research", knowledge="v1")
    legacy = ProjectNode(
        project_id=project["project_id"],
        agent_id="main",
        name="Research",
        knowledge="x" * 30_000,
    )
    await manager._storage.upsert_project(legacy)
    await manager.create(SESSION_KEY, agent_id="main", project_id=project["project_id"])

    merged = await runner._augment_extra_context_with_project_knowledge(
        session_key=SESSION_KEY, extra_context=None
    )
    block = merged["Project Knowledge"]
    assert "[project knowledge truncated]" in block
    # Wrapper adds a bounded envelope; the payload itself is capped.
    assert len(block) < cap + 500


@pytest.mark.asyncio
async def test_missing_session_manager_is_a_noop():
    runner = TurnRunner(
        provider_selector=None,
        config=SimpleNamespace(
            workspace_dir=".",
            memory=SimpleNamespace(source="workspace"),
            tools=SimpleNamespace(profile=None),
        ),
    )
    context = {"Existing": "kept"}
    merged = await runner._augment_extra_context_with_project_knowledge(
        session_key=SESSION_KEY, extra_context=context
    )
    assert merged is context


@pytest.mark.asyncio
async def test_storage_failure_is_best_effort(runner, manager, monkeypatch):
    await manager.create(SESSION_KEY, agent_id="main")

    async def _boom(_key: str) -> str | None:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(manager, "get_project_knowledge_for_session", _boom)
    context = {"Existing": "kept"}
    merged = await runner._augment_extra_context_with_project_knowledge(
        session_key=SESSION_KEY, extra_context=context
    )
    assert merged is context
