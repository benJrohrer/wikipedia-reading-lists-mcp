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
    setup
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

ERROR_HINTS: dict[str, str] = {
    "readinglists-db-error-not-set-up": ("The user has not opted in to the Reading List feature. "
        "Tell the user to enable the feature. The server cannot do this yet."),
    "network": "A network error occurred. Tell the user to check the internet connection.",
    "timeout": ("The connection timed out. Retry once; if it fails again, tell the user to "
        "check the internet connection."),
    "401": ("The user's token is bad. The user must place a proper token in the WIKIMEDIA_ACCESS_TOKEN "
        "environment variable or .env file. The server must be restarted for this to take effect."),
    "403": ("The user's token does not grant them permission to do the requested action. "
        "Tell the user to check if their token has the 'Access private information' grant."),
    "429": "The connection is rate-limited. Wait and retry."
}


def _error_hint(code: str | None) -> str | None:
    """Return the hint for an error code.

    Looks up code in ERROR_HINTS dict to see if a hint is defined.
    For 500 errors, returns generic internal error message. Otherwise returns None.
    """
    if not code:
        return None

    if code in ERROR_HINTS:
        return ERROR_HINTS[code]
    elif code.isdecimal() and code.startswith("5"):
        return "Wikipedia returned an internal error. Try again later."
    else:
        return None


def handle_wikimedia_errors(fn):
    """Re-raise WikimediaAPIError from a tool as ToolError, so the model sees why it failed."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwds):
        try:
            return await fn(*args, **kwds)
        except WikimediaAPIError as e:
            hint = _error_hint(e.code)
            message = (str(e) + f" HINT: {hint}") if hint else str(e)
            raise ToolError(message) from e
    return wrapper


@mcp.tool()
@handle_wikimedia_errors
async def setup_reading_lists(ctx: Context) -> bool:
    """Opt the user in to the Reading Lists feature.
    
    Only use this tool if you have explicit permission of the user.
    Use this to enable the reading list feature on the users account.
    Returns true if the user already was opted-in or if opt-in was succesful.
    Otherwise, raises WikimediaAPIError.
    """
    app: AppContext = ctx.request_context.lifespan_context
    return await setup(app.client)

    
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
