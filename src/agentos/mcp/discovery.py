"""MCP tool discovery and registration into AgentOS ToolRegistry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

from agentos.mcp.client import MCPClient
from agentos.mcp.types import MCPServerConfig, MCPToolDef
from agentos.tools.registry import ToolRegistry
from agentos.tools.schema_sanitize import sanitize_input_schema
from agentos.tools.types import ToolHandler, ToolSpec

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MCPToolRegistration:
    """One registry entry owned by an MCP client."""

    spec: ToolSpec
    handler: ToolHandler

    @property
    def name(self) -> str:
        return self.spec.name


@dataclass(frozen=True)
class ActiveMCPClient:
    """Tracked MCP client with the owner that controls its lifecycle."""

    owner: str
    server_name: str
    transport: str
    client: MCPClient
    registered_tools: tuple[str, ...] = ()
    tool_registrations: tuple[MCPToolRegistration, ...] = ()

    async def close(self) -> None:
        await self.client.close()


# Module-level registry to keep clients alive for tool handlers.
_active_clients: list[ActiveMCPClient] = []


def active_clients_snapshot() -> tuple[ActiveMCPClient, ...]:
    """Return active MCP clients without exposing mutable runtime state."""
    return tuple(_active_clients)


async def close_active_clients(owner: str | None = None) -> int:
    """Close active MCP clients, optionally scoped to one owner/server name."""
    remaining: list[ActiveMCPClient] = []
    closing: list[ActiveMCPClient] = []
    for entry in _active_clients:
        if owner is None or entry.owner == owner or entry.server_name == owner:
            closing.append(entry)
        else:
            remaining.append(entry)
    _active_clients[:] = remaining

    closed = 0
    for entry in closing:
        try:
            await entry.close()
            closed += 1
        except Exception:
            pass
    return closed


async def disconnect_and_unregister(owner: str, registry: ToolRegistry) -> int:
    """Close one MCP server and remove the tools registered by that server."""
    entries = [
        entry
        for entry in active_clients_snapshot()
        if entry.owner == owner or entry.server_name == owner
    ]
    closing_ids = {id(entry) for entry in entries}
    remaining = [entry for entry in _active_clients if id(entry) not in closing_ids]
    affected_names = {
        registration.name for entry in entries for registration in entry.tool_registrations
    }
    for name in affected_names:
        current = registry.get(name)
        if current is None:
            continue
        current_is_closing = any(
            registration.name == name and registration.handler is current.handler
            for entry in entries
            for registration in entry.tool_registrations
        )
        if not current_is_closing:
            continue
        replacement = next(
            (
                registration
                for entry in reversed(remaining)
                for registration in reversed(entry.tool_registrations)
                if registration.name == name
            ),
            None,
        )
        registry.unregister(name)
        if replacement is not None:
            registry.register(replacement.spec, replacement.handler)
    return await close_active_clients(owner)


def create_client(config: MCPServerConfig) -> MCPClient:
    """Factory: create the appropriate MCPClient for the given transport."""
    if config.transport == "stdio":
        from agentos.mcp.stdio import MCPStdioClient

        return MCPStdioClient(config)
    elif config.transport == "sse":
        from agentos.mcp.sse import MCPSSEClient

        return MCPSSEClient(config)
    elif config.transport == "streamable_http":
        from agentos.mcp.streamable_http import MCPStreamableHTTPClient

        return MCPStreamableHTTPClient(config)
    else:
        raise ValueError(f"Unknown MCP transport: {config.transport!r}")


def _make_tool_handler(
    client: MCPClient,
    tool_name: str,
    tool_def: MCPToolDef,
    registry: ToolRegistry,
    timeout_seconds: float,
) -> MCPToolRegistration:
    """Register a single MCP tool into the registry with an mcp_ prefix."""
    # The server's schema goes out verbatim in every provider request, so it is
    # normalized once here rather than per turn. A shape one backend tolerates
    # can make another reject the whole call, tools and all.
    schema, fixes = sanitize_input_schema(tool_def.input_schema)
    if fixes:
        log.info("mcp.schema_sanitized", tool=tool_name, fixes=fixes)
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    spec = ToolSpec(
        name=f"mcp_{tool_name}",
        description=tool_def.description,
        parameters=properties,
        required=required,
    )

    async def handler(**kwargs: Any) -> str:
        try:
            result = await asyncio.wait_for(
                client.call_tool(tool_name, kwargs),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return f"MCP tool '{tool_name}' timed out after {timeout_seconds}s"
        return result.content

    registry.register(spec, handler)
    return MCPToolRegistration(spec=spec, handler=handler)


async def discover_and_register(
    config: MCPServerConfig,
    registry: ToolRegistry,
    *,
    owner: str | None = None,
) -> list[str]:
    """Connect to MCP server, list tools, register each as a AgentOS tool.

    Returns list of registered tool names.
    The client is kept alive in module-level _active_clients so tool handlers can use it.
    """
    client = create_client(config)
    entry: ActiveMCPClient | None = None

    registered: list[str] = []
    registrations: list[MCPToolRegistration] = []
    try:
        await client.connect()
        tools = await client.list_tools()
        for t in tools:
            registration = _make_tool_handler(
                client,
                t.name,
                t,
                registry,
                timeout_seconds=config.tool_timeout_seconds,
            )
            registered.append(registration.name)
            registrations.append(registration)
        entry = ActiveMCPClient(
            owner=owner or config.name,
            server_name=config.name,
            transport=config.transport,
            client=client,
            registered_tools=tuple(registered),
            tool_registrations=tuple(registrations),
        )
        _active_clients.append(entry)
    except BaseException:
        if entry is not None:
            try:
                _active_clients.remove(entry)
            except ValueError:
                pass
        await client.close()
        raise
    return registered
