"""MCP server exposing the user's Wikipedia reading lists as tools."""

import functools
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx2
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

# Absolute import: MCP clients may run this file as a script, where relative
# imports fail.
from wikipedia_reading_lists_mcp.wikimedia import (
    CreateEntryResult,
    CreateListsResult,
    DeleteEntriesResult,
    EntrySpec,
    FetchedEntryResult,
    ListResult,
    ListSpec,
    WikimediaAPIError,
    create_entries,
    create_lists,
    delete_entries,
    delete_list as delete_list_wiki,
    fetch_entries,
    fetch_lists,
    make_client,
    opt_in,
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

ERROR_HINTS: dict[str, str] = {
    "readinglists-db-error-cannot-delete-default-list": (
        "The default list cannot be deleted. Do not retry this call and let the user "
        "know about this limitation. If that list was passed by mistake, use "
        "list_reading_lists to get all of the user's lists with their ids."
    ),
    "readinglists-db-error-not-set-up": (
        "The user has not opted in to the Reading List feature. "
        "To enable the feature, ask the user if you can opt in to this feature on "
        "their behalf. Then, and only with their explicit approval, can you use the "
        "setup_reading_lists tool to enable this feature."
    ),
    "readinglists-db-error-no-such-list": (
        "No list exists with the given id. Call "
        "list_reading_lists to get all lists with their ids. Then you can use an id "
        "from that list to reference it where a list id is required."
    ),
    "readinglists-db-error-no-such-list-entry": (
        "No list entry exists with the given id. Call list_entries with the given "
        "list_id. Then you can use an id from that list to reference it where an entry "
        "id is required. Call list_reading_lists to get all lists, if you need a "
        "list_id as well."
    ),
    "readinglists-db-error-not-own-list": (
        "The user does not own the list. Call list_reading_lists to get the "
        "lists which belong to the user, and use those list ids to perform other "
        "operations which require a list_id."
    ),
    "readinglists-db-error-not-own-list-entry": (
        "The user does not own the list entry. Call list_reading_lists to get the "
        "lists which belong to the user, then list_entries with one of those list ids "
        "to get the entries on that list."
    ),
    "readinglists-db-error-no-such-project": (
        "The given project name is not a recognized Wikimedia project. A project name "
        "is the base url such as 'https://en.wikipedia.org' for the English language "
        "wikipedia project. This error interrupts the create_list_entries tool. Any "
        "entries before the entry with an incorrect project name were created, and any "
        "entries following the entry that caused this error will not have been "
        "created. Use the correct project name for any entries you were trying to "
        "create that used the incorrect project name and submit the whole batch again. "
        "After this subsequent call, and in this case only, ignore the duplicate flag "
        "on the responses which came before the first entry with the incorrect "
        "project, and do not report these to the user as it may just indicate that "
        "these response were added in the preceding call before the incorrect project "
        "name. Notify the user of the updated project name and for which entries it "
        "was used for."
    ),
    "network": (
        "A network error occurred. Tell the user to check the internet connection."
    ),
    "timeout": (
        "The connection timed out. Retry once; if it fails again, tell the user to "
        "check the internet connection."
    ),
    "401": (
        "The user's token is bad. The user must place a proper token in the "
        "WIKIMEDIA_ACCESS_TOKEN environment variable or .env file. The server must be "
        "restarted for this to take effect."
    ),
    "403": (
        "The user's token does not grant them permission to do the requested action. "
        "Tell the user to check if their token has the 'Access private information' "
        "grant."
    ),
    "429": "The connection is rate-limited. Wait and retry.",
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
    """Re-raise WikimediaAPIError from a tool as ToolError.

    Re-raising the error as a ToolError ensures the model sees why it failed.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwds):
        try:
            return await fn(*args, **kwds)
        except WikimediaAPIError as e:
            hint = _error_hint(e.code)
            message = (str(e) + f" HINT: {hint}") if hint else str(e)
            raise ToolError(message) from e

    return wrapper


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
    other reading-list tools.
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
        hint = _error_hint(result["failed"]["code"])
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


def main() -> None:
    """Run the server over stdio. Entry point for the console script."""
    mcp.run()


if __name__ == "__main__":
    main()
