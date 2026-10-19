"""MCP server exposing the user's Wikipedia reading lists as tools."""

import functools
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx2
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

# Absolute import: MCP clients may run this file as a script, where relative
# imports fail.
from wikipedia_reading_lists_mcp.wikimedia import (
    WikimediaAPIError,
    fetch_lists,
    make_client,
)


@dataclass
class AppContext:
    """State shared by every tool call for the lifetime of the server."""

    client: httpx2.AsyncClient


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncGenerator[AppContext]:
    """Create the shared Wikimedia client at startup and close it on shutdown."""
    async with make_client() as client:
        yield AppContext(client)


mcp = MCPServer("wikipedia-reading-lists", lifespan=lifespan)


def handle_wikimedia_errors(fn):
    """Re-raise WikimediaAPIError from a tool as ToolError, so the model sees why it failed."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwds):
        try:
            return await fn(*args, **kwds)
        except WikimediaAPIError as e:
            raise ToolError(str(e)) from e
    return wrapper


@mcp.tool()
@handle_wikimedia_errors
async def list_reading_lists(ctx: Context) -> list[dict]:
    """List the user's Wikipedia reading lists.

    Use this to see which reading lists exist before reading or changing one.
    Returns every list (all pages are fetched), each with "id", "name",
    "created", and "updated", and sometimes "description". Use the "id" with
    other reading-list tools.
    """
    app: AppContext = ctx.request_context.lifespan_context
    return await fetch_lists(app.client)


if __name__ == "__main__":
    mcp.run()
