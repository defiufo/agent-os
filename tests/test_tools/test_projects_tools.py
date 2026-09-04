"""Tests for the projects_* builtin tools and project-scoped session_search.

The tools run against the REAL ``SessionManager`` over in-memory storage so a
test can never pass against a method the live gateway does not have.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from agentos.session.manager import SessionManager
from agentos.session.storage import SessionStorage
from agentos.tools.builtin import projects as projects_tool
from agentos.tools.builtin.session_search import create_session_search_tool
from agentos.tools.registry import ToolRegistry
from agentos.tools.types import ToolContext, ToolError, current_tool_context

SESSION_KEY = "agent:main:webchat:cafe0001"


@pytest_asyncio.fixture
async def storage():
    store = SessionStorage(":memory:")
    await store.connect()
    yield store
    await store.close()


@pytest_asyncio.fixture
async def manager(storage):
    mgr = SessionManager(storage, inject_time_prefix=False)
    original = projects_tool._session_manager
    projects_tool.set_session_manager(mgr)
    yield mgr
    projects_tool.set_session_manager(original)


def _set_ctx(session_key: str | None, agent_id: str = "main"):
    return current_tool_context.set(ToolContext(session_key=session_key, agent_id=agent_id))


@pytest.mark.asyncio
async def test_projects_create_defaults_agent_from_context(manager):
    token = _set_ctx(SESSION_KEY, agent_id="ops")
    try:
        data = json.loads(await projects_tool.projects_create(name="Research"))
    finally:
        current_tool_context.reset(token)
    assert data["agent_id"] == "ops"
    assert data["name"] == "Research"


@pytest.mark.asyncio
async def test_projects_list_returns_counts(manager):
    project = json.loads(await projects_tool.projects_create(name="Research", agent_id="main"))
    await manager.create(SESSION_KEY, agent_id="main", project_id=project["project_id"])

    rows = json.loads(await projects_tool.projects_list())
    assert [(row["name"], row["session_count"]) for row in rows] == [("Research", 1)]


@pytest.mark.asyncio
async def test_projects_update_replaces_knowledge_of_own_project(manager):
    project = json.loads(
        await projects_tool.projects_create(name="Research", agent_id="main", knowledge="v1")
    )
    await manager.create(SESSION_KEY, agent_id="main", project_id=project["project_id"])

    token = _set_ctx(SESSION_KEY)
    try:
        data = json.loads(
            await projects_tool.projects_update(project_id=project["project_id"], knowledge="v2")
        )
    finally:
        current_tool_context.reset(token)
    assert data["knowledge"] == "v2"


@pytest.mark.asyncio
async def test_projects_update_foreign_project_is_denied(manager):
    """A session in project A must not be able to write project B's knowledge."""
    own = json.loads(await projects_tool.projects_create(name="Own", agent_id="main"))
    other = json.loads(await projects_tool.projects_create(name="Other", agent_id="main"))
    await manager.create(SESSION_KEY, agent_id="main", project_id=own["project_id"])

    token = _set_ctx(SESSION_KEY)
    try:
        with pytest.raises(ToolError, match="calling session"):
            await projects_tool.projects_update(
                project_id=other["project_id"], knowledge="injected"
            )
    finally:
        current_tool_context.reset(token)


@pytest.mark.asyncio
async def test_projects_update_outside_any_project_is_denied(manager):
    """Sessions without a project (or without context) get the scoping error,
    not an existence oracle for foreign project ids."""
    await manager.create(SESSION_KEY, agent_id="main")

    token = _set_ctx(SESSION_KEY)
    try:
        with pytest.raises(ToolError, match="calling session"):
            await projects_tool.projects_update(project_id="missing", knowledge="v2")
    finally:
        current_tool_context.reset(token)

    with pytest.raises(ToolError, match="calling session"):
        await projects_tool.projects_update(project_id="missing", knowledge="v2")


@pytest.mark.asyncio
async def test_projects_move_session_defaults_to_calling_session(manager):
    project = json.loads(await projects_tool.projects_create(name="Research", agent_id="main"))
    await manager.create(SESSION_KEY, agent_id="main")

    token = _set_ctx(SESSION_KEY)
    try:
        data = json.loads(
            await projects_tool.projects_move_session(project_id=project["project_id"])
        )
    finally:
        current_tool_context.reset(token)
    assert data == {"session_key": SESSION_KEY, "project_id": project["project_id"]}

    # Detach by omitting project_id.
    token = _set_ctx(SESSION_KEY)
    try:
        data = json.loads(await projects_tool.projects_move_session())
    finally:
        current_tool_context.reset(token)
    assert data["project_id"] is None


@pytest.mark.asyncio
async def test_projects_move_session_without_context_is_denied(manager):
    with pytest.raises(ToolError, match="requires a calling session"):
        await projects_tool.projects_move_session(project_id="whatever")


@pytest.mark.asyncio
async def test_projects_move_session_foreign_session_is_denied(manager):
    """Only the calling session may be moved — a foreign key would hand the
    project's knowledge to another session's system prompt."""
    project = json.loads(await projects_tool.projects_create(name="Research", agent_id="main"))
    await manager.create(SESSION_KEY, agent_id="main")
    victim = await manager.create("agent:main:webchat:cafe0009", agent_id="main")

    token = _set_ctx(SESSION_KEY)
    try:
        with pytest.raises(ToolError, match="only move the calling session"):
            await projects_tool.projects_move_session(
                project_id=project["project_id"], session_key=victim.session_key
            )
    finally:
        current_tool_context.reset(token)
    moved = await manager.get_session(victim.session_key)
    assert moved.project_id is None


@pytest.mark.asyncio
async def test_projects_list_hides_foreign_knowledge(manager):
    """Knowledge text is returned only for the calling session's own project."""
    own = json.loads(
        await projects_tool.projects_create(name="Own", agent_id="main", knowledge="mine")
    )
    json.loads(
        await projects_tool.projects_create(name="Other", agent_id="main", knowledge="theirs")
    )
    await manager.create(SESSION_KEY, agent_id="main", project_id=own["project_id"])

    token = _set_ctx(SESSION_KEY)
    try:
        rows = json.loads(await projects_tool.projects_list())
    finally:
        current_tool_context.reset(token)
    by_name = {row["name"]: row for row in rows}
    assert by_name["Own"]["knowledge"] == "mine"
    assert "knowledge" not in by_name["Other"]

    # Without any calling session, no knowledge is exposed at all.
    rows = json.loads(await projects_tool.projects_list())
    assert all("knowledge" not in row for row in rows)


@pytest.mark.asyncio
async def test_session_search_project_scope(manager, storage):
    registry = ToolRegistry()
    create_session_search_tool(storage, registry=registry)
    registered = registry.get("session_search")
    assert registered is not None
    search = registered.handler

    project = json.loads(await projects_tool.projects_create(name="Research", agent_id="main"))
    inside = await manager.create(SESSION_KEY, agent_id="main", project_id=project["project_id"])
    outside = await manager.create("agent:main:webchat:cafe0002", agent_id="main")
    await manager.append_message(inside.session_key, role="user", content="quantum widget alpha")
    await manager.append_message(outside.session_key, role="user", content="quantum widget beta")

    # Unscoped search sees both sessions.
    data = json.loads(await search(query="quantum widget"))
    assert data["result_count"] == 2

    # Project scope restricts to sibling sessions of the calling session.
    token = _set_ctx(inside.session_key)
    try:
        data = json.loads(await search(query="quantum widget", scope="project"))
    finally:
        current_tool_context.reset(token)
    assert data["result_count"] == 1
    assert data["results"][0]["session_key"] == inside.session_key


@pytest.mark.asyncio
async def test_session_search_project_scope_outside_project_notes(manager, storage):
    registry = ToolRegistry()
    create_session_search_tool(storage, registry=registry)
    search = registry.get("session_search").handler

    outside = await manager.create("agent:main:webchat:cafe0003", agent_id="main")
    token = _set_ctx(outside.session_key)
    try:
        data = json.loads(await search(query="anything", scope="project"))
    finally:
        current_tool_context.reset(token)
    assert data["results"] == []
    assert "not in a project" in data["note"]
