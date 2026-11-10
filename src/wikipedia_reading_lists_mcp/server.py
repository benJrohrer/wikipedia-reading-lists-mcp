"""MCP server exposing the user's Wikipedia reading lists as tools."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx2
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations

# Absolute import: MCP clients may run this file as a script, where relative
# imports fail.
from wikipedia_reading_lists_mcp.http_client import make_client
from wikipedia_reading_lists_mcp.models import (
    CreateEntryResult,
    CreateListsResult,
    DeleteEntriesResult,
    EntrySpec,
    FetchedEntryResult,
    ListResult,
    ListSpec,
)
from wikipedia_reading_lists_mcp.tool_errors import (
    error_hint,
    handle_wikimedia_errors,
)
from wikipedia_reading_lists_mcp.wikimedia import (
    create_entries,
    create_lists,
    delete_entries,
    delete_list as delete_list_wiki,
    fetch_entries,
    fetch_lists,
    opt_in,
    update_list as update_list_wiki,
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


SERVER_INSTRUCTIONS = (
    "Before creating reading lists, the user's account must be opted in to the "
    "Reading Lists feature. If you're unsure whether it is, ask the user for "
    "permission and call setup_reading_lists first. A False return means they "
    "were already opted in."
)

mcp = MCPServer(
    "wikipedia-reading-lists", lifespan=lifespan, instructions=SERVER_INSTRUCTIONS
)


@mcp.tool(
    annotations=ToolAnnotations(
        idempotent_hint=True,
        destructive_hint=False,
        open_world_hint=False,
    )
)
@handle_wikimedia_errors
async def setup_reading_lists(ctx: Context) -> bool:
    """Opt the user in to Wikipedia's Reading Lists feature.

    Only use this tool if you have explicit permission of the user.
    Use this to enable the reading list feature on the user's account.
    Returns true if and only if opt-in was successful.
    Returns false if the user was already opted in. Do not treat a False
    return as meaning the user is not opted in.
    It just means that they were already opted in.
    Otherwise, raises a ToolError.
    Treat any bool return value as meaning the user is opted-in.
    """
    app: AppContext = ctx.request_context.lifespan_context
    return await opt_in(app.client)


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=True,
        open_world_hint=False,
    )
)
@handle_wikimedia_errors
async def list_reading_lists(ctx: Context) -> list[ListResult]:
    """List the user's Wikipedia reading lists.

    Use this to see which reading lists exist before reading or changing one.
    Returns every list (all pages are fetched), each with "id", "name",
    "created", and "updated", and sometimes "description". Use the "id" with
    other reading-list tools. The results will also indicate whether or not a list is
    the default list. Pay attention to this, especially for making subsequent calls to
    update_list and delete_list, which cannot be performed on the default list.
    """
    app: AppContext = ctx.request_context.lifespan_context
    return await fetch_lists(app.client)


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=True,
        open_world_hint=False,
    )
)
@handle_wikimedia_errors
async def list_entries(ctx: Context, list_id: int) -> list[FetchedEntryResult]:
    """List all entries for the user's Wikipedia reading list with the given id.

    Get list_id from another tool such as list_reading_lists.
    Use this to list all articles in the reading list denoted by the given id.
    Returns every entry (all pages are fetched), each with
    'id', 'project', 'title', 'created', and 'updated'.
    Use 'title' to lookup articles in the given 'project'.
    """
    app: AppContext = ctx.request_context.lifespan_context
    return await fetch_entries(app.client, list_id)


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False,
        open_world_hint=False,
        idempotent_hint=True,
        destructive_hint=False,
    )
)
@handle_wikimedia_errors
async def create_reading_lists(
    ctx: Context, lists: list[ListSpec]
) -> CreateListsResult:
    """Create one or more Wikipedia reading lists with the given names and descriptions.

    Only use this tool with the explicit permission of the user.
    If the user has not given a specific name for the list,
    come up with one that matches the content as closely as possible.
    Before using this tool, use list_reading_lists to check if the given name conflicts.
    This creates one or more reading lists on the user's account.
    Including duplicate names in the input will only attempt to create the last copy.
    The response will be a dictionary containing 3 lists:
        created: a list of the reading lists created.
        updated: a list of the reading lists whose descriptions were updated because of
            a name conflict with an existing list, despite attempts to exclude such
            lists beforehand. This only happens in a rare race case. Notify the user
            that the description for all reading lists in this list was updated.
        excluded: a list of the reading lists that were excluded from being created
            because they were found to have conflicting names with already existing
            reading lists. Notify the user that these lists were excluded. Ask the user
            if they want to use the existing list (if you are in the process of using it
            for something else), choose a different name and attempt creating it again,
            or skip creating it.
    Each item of all 3 lists includes the list id, which you can use for future list
    operations, such as adding or updating entries.
    """
    app: AppContext = ctx.request_context.lifespan_context
    return await create_lists(app.client, lists)


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False,
        open_world_hint=False,
        idempotent_hint=True,
        destructive_hint=False,
    )
)
@handle_wikimedia_errors
async def create_list_entries(
    ctx: Context, list_id: int, entries: list[EntrySpec]
) -> list[CreateEntryResult]:
    """Create one or more entries on the reading list defined by list_id.

    Only use this tool with the explicit permission of the user.
    This creates one or more entries on the reading list defined by list_id
    with the given project and title. list_id comes from other tools such as
    list_reading_lists or create_reading_lists. The title of the page to be added is the
    ending of its URL in the given project, and the project is the base of the URL. For
    example, for the page at https://en.wikipedia.org/wiki/Claude_(AI), the project is
    'https://en.wikipedia.org' and the title is 'Claude_(AI)'. Use this schema to create
    reading list entries from wikipedia links. Use the decoded title with no fragment.
    If an entry already exists on the list it will not be added again nor updated.
    The return value will be a list of created entries or duplicates. Entries identified
    as duplicates will have their duplicate field set to true. Notify the user of any
    duplicate entries. This tool can have partial failures: if one entry in the batch
    causes an error, all entries before it will have been created, but the errant entry
    and others after it will not have been created.
    """
    app: AppContext = ctx.request_context.lifespan_context
    return await create_entries(app.client, list_id, entries)


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False,
        open_world_hint=False,
        idempotent_hint=True,
        destructive_hint=True,
    )
)
@handle_wikimedia_errors
async def delete_list_entries(
    ctx: Context, list_id: int, entry_ids: list[int]
) -> DeleteEntriesResult:
    """Delete one or more entries from the reading list defined by list_id.

    Only use this tool with the explicit permission of the user. This deletes the
    entries with the given entry_ids from the reading list defined by list_id. list_id
    comes from other tools such as list_reading_lists or create_reading_lists, and
    entry_ids come from list_entries or create_list_entries. To delete entries from
    several lists, call this tool once per list. If the list can't be read, this tool
    raises an error and nothing is deleted. Otherwise the return value is a dictionary
    with these keys:
    - deleted: ids of entries successfully deleted
    - failed: null, or the entry whose deletion hit an unexpected error, with its
      entry_id, code, message, and sometimes a hint on how to proceed. The tool stops
      at this error. The entry may or may not have been deleted, so do not tell the
      user it is still on the list.
    - excluded: ids of entries not deleted because they are not on the given list. The
      tool can't tell why: an entry may be on another list or may already be deleted.
    - not_attempted: ids of entries skipped because the tool stopped at failed
    Notify the user of the entries that were deleted, and of any that were not and why.
    Do not retry excluded entries on the same list. If you expected one to be on this
    list, check that list_id is correct. To finish after a failure, follow the hint if
    there is one, then call this tool again with the failed entry's id and the
    not_attempted ids. It is safe to retry the failed entry: if it was deleted, it will
    be reported as excluded.
    """
    app: AppContext = ctx.request_context.lifespan_context
    result = await delete_entries(app.client, list_id, entry_ids)
    # ToolError would hide the entries already deleted, so the hint goes in the result
    if result["failed"]:
        hint = error_hint(result["failed"]["code"])
        if hint:
            result["failed"]["hint"] = hint
    return result


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False,
        open_world_hint=False,
        idempotent_hint=True,
        destructive_hint=True,
    )
)
@handle_wikimedia_errors
async def delete_list(ctx: Context, list_id: int) -> bool:
    """Delete an entire list with the given list_id from the user's account.

    Only use this tool with the explicit permission of the user. This deletes the
    list defined by list_id. list_id comes from other tools such as list_reading_lists
    or create_reading_lists. When a list is deleted, all of the entries on that list
    are deleted too; the same article on other lists is not affected. Deleting a list
    cannot be undone. The default list cannot be deleted. List responses (like from
    calling list_reading_lists) will include a default field, and you should check this
    and avoid calling this tool on the default list.
    Returns true if the delete was successful, or false if it had already been deleted.
    Otherwise, raises a ToolError.
    Do not treat a false return as meaning the list was not deleted. It just means that
    the list was already deleted.
    """
    app: AppContext = ctx.request_context.lifespan_context
    return await delete_list_wiki(app.client, list_id)


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False,
        open_world_hint=False,
        idempotent_hint=True,
        destructive_hint=True,
    )
)
@handle_wikimedia_errors
async def update_list(
    ctx: Context, list_id: int, name: str | None = None, description: str | None = None
) -> ListResult:
    """Update the list defined by list_id with the given name and/or description.

    Only use this tool with the explicit permission of the user.
    This updates the list with list_id to have the given name and/or description. One
    of either name or description is required to use this tool. Not including one of
    name or description is allowed, and the excluded argument will be ignored and not
    updated. If name is included, it cannot be the empty string. When description is the
    empty string, it clears the description of the list. The default list cannot be
    updated. A list cannot be updated to have the same name as another list. List
    responses (like from calling list_reading_lists) will include a id field (to use as
    list_id), a default field, and the name of the list. You should check this and avoid
    calling this tool on the default list or with an already existing list name.
    Returns the updated list.
    """
    app: AppContext = ctx.request_context.lifespan_context
    return await update_list_wiki(
        app.client, list_id, name=name, description=description
    )


def main() -> None:
    """Run the server over stdio. Entry point for the console script."""
    mcp.run()


if __name__ == "__main__":
    main()
