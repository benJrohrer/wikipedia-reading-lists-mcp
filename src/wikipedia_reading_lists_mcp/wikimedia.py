"""Wikimedia reading-list API calls (ADR 0002 API choice).

Each function sends its requests through http_client and returns plain data.
"""

import asyncio
import logging
import sys

import httpx2

# Absolute import: this file also runs as a script (the smoke test), where
# relative imports fail.
from wikipedia_reading_lists_mcp import http_client
from wikipedia_reading_lists_mcp.errors import WikimediaAnonError, WikimediaAPIError
from wikipedia_reading_lists_mcp.models import (
    CreateEntryResult,
    CreateListsResult,
    DeleteEntriesResult,
    EntrySpec,
    FailedDelete,
    FetchedEntryResult,
    ListResult,
    ListSpec,
)

# Each request names its full path from the domain root (see http_client). The
# Action API is a single endpoint; the reading-list REST endpoints live under the
# public rest_v1 prefix (ADR 0002).
ACTION_API = "w/api.php"
READING_LISTS_API = "api/rest_v1/data/lists/"
CREATE_LIST_BATCH_API = READING_LISTS_API + "batch"
logger = logging.getLogger(__name__)


async def opt_in(client: httpx2.AsyncClient) -> bool:
    """Opt-in the user to the Reading Lists feature.

    Returns true if the request succeeded.
    Return false if the user was already opted in.
    Otherwise, raises a WikimediaAPIError
    """

    already_set_up = "readinglists-db-error-already-set-up"
    setup_api = READING_LISTS_API + "setup"
    try:
        await http_client.post(client, setup_api)
    except WikimediaAPIError as e:
        if e.code == already_set_up:
            return False
        else:
            raise

    return True


async def fetch_username(client: httpx2.AsyncClient) -> str:
    """Return the username the token authenticates as.

    Raises WikimediaAPIError if the request fails or the API reports an
    anonymous user.
    """
    query_params = {
        "action": "query",
        "meta": "userinfo",
        "format": "json",
        "formatversion": "2",
    }
    response = await http_client.get(client, ACTION_API, query_params)

    data = response.json()
    if "warnings" in data:
        for value in data["warnings"].values():
            logger.warning(value["warnings"])

    if "error" in data:
        error = data["error"]
        raise WikimediaAPIError(f"{error['code']}: {error['info']}", error["code"])

    query = data["query"]
    userinfo = query["userinfo"]
    if "anon" in userinfo:
        raise WikimediaAnonError()

    return userinfo["name"]


async def fetch_lists(client: httpx2.AsyncClient) -> list[ListResult]:
    """Return the current user's reading lists from GET /data/lists/.

    Each list is a dict with at least "id", "name", "created", and "updated"
    (rest_v1 spec, list_read schema). Raises WikimediaAPIError if the request
    fails. Follows `next` continuation tokens until all pages are fetched.
    """
    lists = []
    query_params = {}
    while True:
        response = await http_client.get(client, READING_LISTS_API, query_params)

        data = response.json()
        lists.extend(data["lists"])

        if "next" not in data:
            break

        next_token = data["next"]
        # Guard in case we somehow get stuck in a next loop
        if query_params.get("next") == next_token:
            raise WikimediaAPIError(
                (
                    f"Received the same next token ({next_token}) "
                    "twice while fetching lists."
                ),
                "pagination",
            )

        query_params["next"] = data["next"]

    return lists


async def fetch_entries(
    client: httpx2.AsyncClient, list_id: int
) -> list[FetchedEntryResult]:
    """Return the entries on the user's reading list with the given list_id.

    Returns every entry (all pages are fetched), each with
    'id', 'project', 'title', 'created', and 'updated'.
    Raises WikimediaAPIError if the request fails. Follows `next` continuation tokens
    until all pages are fetched.
    """

    list_entries_api = READING_LISTS_API + f"{list_id}/entries/"
    entries = []
    query_params = {}
    while True:
        response = await http_client.get(client, list_entries_api, query_params)

        data = response.json()
        entries.extend(data["entries"])

        if "next" not in data:
            break

        next_token = data["next"]
        # Guard in case we somehow get stuck in a next loop
        if query_params.get("next") == next_token:
            raise WikimediaAPIError(
                (
                    f"Received the same next token ({next_token}) "
                    "twice while fetching entries."
                ),
                "pagination",
            )

        query_params["next"] = data["next"]

    return entries


async def create_lists(
    client: httpx2.AsyncClient,
    lists_to_create: list[ListSpec],
) -> CreateListsResult:
    """Create the lists defined by the list of ListSpecs.

    Builds a batch dictionary from lists_to_create, excluding lists already existing and
    known from calling fetch_lists. Returns a dictionary of 3 lists: the reading lists
    created, the reading lists whose descriptions were updated (rare race condition),
    and the reading lists that were excluded from being created because one with the
    same name already existed.
    """

    existing_lists = await fetch_lists(client)
    existing_by_name: dict[str, dict] = {
        reading_list["name"]: reading_list for reading_list in existing_lists
    }
    create_by_name = {
        list_to_create["name"]: list_to_create for list_to_create in lists_to_create
    }
    excluded_by_name = {
        name: existing_list
        for name, existing_list in existing_by_name.items()
        if name in create_by_name
    }
    batch_list = [
        list_spec
        for name, list_spec in create_by_name.items()
        if name not in excluded_by_name
    ]

    created = []
    updated = []
    excluded = list(excluded_by_name.values())

    if batch_list:
        batch = {"batch": batch_list}
        response = await http_client.post(client, CREATE_LIST_BATCH_API, json=batch)
        data = response.json()
        if "lists" not in data:
            raise WikimediaAPIError(
                "No lists returned after list creation.", "no-lists-created"
            )

        for list_response in data["lists"]:
            if list_response.get("duplicate"):
                updated.append(list_response)
                logger.warning(
                    "Reading list %r (id %s) was unexpectedly updated during a "
                    "call to create_lists (duplicate name).",
                    list_response["name"],
                    list_response["id"],
                )
            else:
                created.append(list_response)

    return {"created": created, "updated": updated, "excluded": excluded}


async def create_entries(
    client: httpx2.AsyncClient,
    list_id: int,
    entries_to_create: list[EntrySpec],
) -> list[CreateEntryResult]:
    """Create the entries defined by the list of EntrySpecs.

    Returns the list of entries created and any duplicates.
    """

    if not entries_to_create:
        return []

    create_entry_batch_api = READING_LISTS_API + f"{list_id}/entries/batch"
    batch = {"batch": entries_to_create}
    response = await http_client.post(client, create_entry_batch_api, json=batch)
    data = response.json()
    if "entries" not in data:
        raise WikimediaAPIError(
            "No entries returned after entry creation.", "no-entries-created"
        )
    return data["entries"]


async def delete_entries(
    client: httpx2.AsyncClient, list_id: int, entry_ids: list[int]
) -> DeleteEntriesResult:
    """Delete the entries with the given entry_ids from the list with the given list_id.

    Repeated ids are only processed once. Stops at the first error other than the entry
    already being deleted, so entries deleted before it are still reported. Returns a
    dictionary with lists of the ids successfully deleted, excluded because they are
    not on the list (on another list or already deleted), and not attempted because of
    an earlier error, along with the failed entry, if any. Raises WikimediaAPIError,
    with nothing deleted, if the list's entries can't be fetched.
    """

    # The DELETE endpoint accepts an entry id under a list it doesn't belong to, so
    # this check is the only thing ensuring entries are deleted from the given list.
    fetched_entries = await fetch_entries(client, list_id)
    valid_entry_ids = {fetched_entry["id"] for fetched_entry in fetched_entries}

    already_deleted_error = "readinglists-db-error-list-entry-deleted"
    unique_entry_ids = list(dict.fromkeys(entry_ids))
    successfully_deleted = []
    failed: FailedDelete | None = None
    excluded = []
    not_attempted = []

    for index, entry_id in enumerate(unique_entry_ids):
        if entry_id not in valid_entry_ids:
            # do not attempt to delete entry because it is not actually on the list
            excluded.append(entry_id)
            continue

        url = READING_LISTS_API + f"{list_id}/entries/{entry_id}"
        try:
            await http_client.delete(client, url)
            successfully_deleted.append(entry_id)
        except WikimediaAPIError as e:
            if e.code == already_deleted_error:
                # the entry left the list after the check, so treat it the same as
                # any other entry that isn't on the list
                excluded.append(entry_id)
            else:
                # the pre-check rules out per-entry errors, so assume the remaining
                # requests would fail too (e.g. bad token, rate limit, network)
                failed = {"entry_id": entry_id, "code": e.code, "message": str(e)}
                not_attempted = unique_entry_ids[index + 1 :]
                break

    return {
        "deleted": successfully_deleted,
        "failed": failed,
        "excluded": excluded,
        "not_attempted": not_attempted,
    }


async def delete_list(
    client: httpx2.AsyncClient,
    list_id: int,
) -> bool:
    """Delete the list given by list_id.

    Returns true if the list was deleted by this call.
    Return false if the list had already been deleted.
    Otherwise, raises a WikimediaAPIError.
    """

    url = READING_LISTS_API + f"{list_id}"
    already_deleted_error = "readinglists-db-error-list-deleted"
    try:
        await http_client.delete(client, url)
    except WikimediaAPIError as e:
        if e.code == already_deleted_error:
            return False
        else:
            raise

    return True


async def _fetch_list_name(
    client: httpx2.AsyncClient,
    list_id: int,
) -> str:
    """Return the name of the list with the given list_id.

    Utility function which calls fetch_lists and returns the name of the list with the
    matching list_id. If no such list is found, raises a WikimediaAPIError.
    """
    lists = await fetch_lists(client)
    for reading_list in lists:
        if reading_list["id"] == list_id:
            return reading_list["name"]

    raise WikimediaAPIError(
        f"No list exists with ID: {list_id}.",
        "readinglists-db-error-no-such-list",
    )


async def update_list(
    client: httpx2.AsyncClient,
    list_id: int,
    name: str | None = None,
    description: str | None = None,
) -> ListResult:
    """Update the list with the given list_id to have the given name and description.

    One of either name or description is required. When name is included, it must be
    non-empty. An empty description clears the list's description. Clearing the
    description (by passing description=""), without including name, will force a call
    to _fetch_list_name and fetch_lists.
    """

    if name is None and description is None:
        raise WikimediaAPIError(
            "One of name or description is required.", "name-or-description-required"
        )
    if name == "":
        raise WikimediaAPIError(
            "Name must not be empty string for update.", "non-empty-name-required"
        )

    url = READING_LISTS_API + f"{list_id}"
    json = {}
    if name is not None:
        json["name"] = name
    elif description == "":
        # The API treats an empty description as missing unless a name is sent with it.
        # When name is None and description is "", we can lookup the name of list_id to
        # clear the description. This is a quirk of the API discussed in (#9)
        json["name"] = await _fetch_list_name(client, list_id)

    if description is not None:
        json["description"] = description

    response = await http_client.put(client, url, json=json)
    return response.json()["list"]


async def _smoke_test() -> None:
    async with http_client.make_client() as client:
        username = await fetch_username(client)
        lists = await fetch_lists(client)
    # stdout carries the MCP protocol, so diagnostics go to stderr.
    print(f"Authenticated as: {username}", file=sys.stderr)
    print(f"Reading lists ({len(lists)}):", file=sys.stderr)
    for reading_list in lists:
        print(f"  {reading_list['id']}: {reading_list['name']}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(_smoke_test())
