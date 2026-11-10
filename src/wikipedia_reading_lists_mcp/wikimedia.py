"""HTTP client for the Wikimedia APIs (ADR 0001 auth, ADR 0002 API choice)."""

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

import httpx2
from dotenv import load_dotenv
from tenacity import (
    RetryCallState,
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    stop_before_delay,
    wait_exponential,
)

# Absolute import: this file also runs as a script (the smoke test), where
# relative imports fail.
from wikipedia_reading_lists_mcp.errors import (
    WikimediaAnonError,
    WikimediaAPIError,
    WikimediaRateLimitError,
)

type HttpMethod = Literal["GET", "POST", "DELETE", "PUT"]

# httpx2 always appends "/" to base_url, so the base is the domain root and
# each request names its full path. The Action API is a single endpoint; the
# reading-list REST endpoints live under the public rest_v1 prefix (ADR 0002).
API_BASE_URL = "https://en.wikipedia.org/"
ACTION_API = "w/api.php"
READING_LISTS_API = "api/rest_v1/data/lists/"
CREATE_LIST_BATCH_API = READING_LISTS_API + "batch"
# Wikimedia's User-Agent policy asks for a client name, version, and contact.
USER_AGENT = (
    "wikipedia-reading-lists-mcp/0.1.0 "
    "(https://github.com/benJrohrer/wikipedia-reading-lists-mcp)"
)
# The server waits out short rate limits (HTTP 429) itself and passes longer ones
# to the model (#11). Wikimedia asks clients without a Retry-After header to wait
# at least 5 seconds or back off exponentially.
RATE_LIMIT_MAX_DELAY = 55.0  # seconds
RATE_LIMIT_MAX_WAIT = 30.0  # seconds
RATE_LIMIT_MAX_RETRIES = 2
RATE_LIMIT_MIN_BACKOFF = 5.0  # seconds
logger = logging.getLogger(__name__)

# Resolve .env from the project root, not the working directory, because
# MCP clients may launch the server from anywhere.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


class CreateEntryResult(TypedDict):
    """A dictionary with fields for an entry created or duplicate response."""

    id: int
    listId: int
    project: str
    title: str
    created: str
    updated: str
    duplicate: bool


class CreateListsResult(TypedDict):
    """A dictionary with the lists of created, updated, and excluded reading lists."""

    created: list[dict]
    updated: list[dict]
    excluded: list[dict]


class FetchedEntryResult(TypedDict):
    """A dictionary with id, project, title, created, and updated fields for entry."""

    id: int
    project: str
    title: str
    created: str
    updated: str


class FailedDelete(TypedDict):
    """The entry whose deletion failed, with the error that stopped the batch."""

    entry_id: int
    code: str | None
    message: str
    hint: NotRequired[str]


class DeleteEntriesResult(TypedDict):
    """A dictionary with the outcome of each entry in a delete attempt."""

    deleted: list[int]
    failed: FailedDelete | None
    excluded: list[int]
    not_attempted: list[int]


class EntrySpec(TypedDict):
    """An entry to create on a reading list."""

    project: str
    title: str


class ListSpec(TypedDict):
    """A reading list to create."""

    name: str
    description: NotRequired[str]


class ListResult(TypedDict):
    """A typical reading list result from listing or updating a reading list."""

    id: int
    name: str
    default: NotRequired[bool]
    description: NotRequired[str]
    created: str
    updated: str


def get_access_token() -> str:
    """Return the owner-only OAuth 2.0 token, or raise if it is missing."""
    token = os.environ.get("WIKIMEDIA_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(
            "WIKIMEDIA_ACCESS_TOKEN is not set. Copy .env.example to .env "
            "and add your token."
        )
    return token


def make_client() -> httpx2.AsyncClient:
    """Create a client that sends the bearer token and User-Agent on every request."""
    return httpx2.AsyncClient(
        base_url=API_BASE_URL,
        headers={
            "Authorization": f"Bearer {get_access_token()}",
            "User-Agent": USER_AGENT,
        },
        timeout=10.0,
    )


def _parse_retry_after(value: str | None) -> float | None:
    """Return the wait in seconds from a Retry-After header value.

    The header holds either a number of seconds or an HTTP date. Returns None if
    the header is missing or can't be read.
    """
    if value is None:
        return None

    seconds = None
    try:
        seconds = max(float(value), 0)
    except ValueError:
        try:
            future_dt = parsedate_to_datetime(value)
            difference: timedelta = future_dt - datetime.now(UTC)
            seconds = max(difference.total_seconds(), 0)
        except (TypeError, ValueError):
            logger.warning(
                (
                    "Unable to parse retry-after header in Wikimedia API 429 error "
                    "response. Retry-After value was: `%s`."
                ),
                value,
            )

    return seconds


def _is_short_rate_limit(exc: BaseException) -> bool:
    """Return True if the exception is a WikimediaRateLimitError with a short wait.

    Also returns True if the wait time is None. Returns False otherwise.
    """
    return isinstance(exc, WikimediaRateLimitError) and (
        exc.wait is None or exc.wait <= RATE_LIMIT_MAX_WAIT
    )


def _rate_limit_wait(retry_state: RetryCallState) -> float:
    """Return the number of seconds to wait.

    This value is either the number of seconds parsed from the API error, or
    the next exponential back off value determined by the retry_state.
    """
    ratelimit_error = retry_state.outcome.exception()
    assert isinstance(ratelimit_error, WikimediaRateLimitError)
    if ratelimit_error.wait is None:
        return wait_exponential(multiplier=RATE_LIMIT_MIN_BACKOFF)(retry_state)

    return ratelimit_error.wait


@retry(
    retry=retry_if_exception(_is_short_rate_limit),
    wait=_rate_limit_wait,
    stop=stop_after_attempt(RATE_LIMIT_MAX_RETRIES + 1)
    | stop_before_delay(RATE_LIMIT_MAX_DELAY),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.INFO),
)
async def _request(
    client: httpx2.AsyncClient,
    method: HttpMethod,
    url: str,
    params: dict | None = None,
    json: dict | None = None,
) -> httpx2.Response:
    """Send an HTTP request with the given method and return the successful response.

    Retries rate-limited requests (#11); see _send_once for everything else it
    raises. Should be called by helper functions which specify the method.
    """
    return await _send_once(client, method, url, params=params, json=json)


async def _send_once(
    client: httpx2.AsyncClient,
    method: HttpMethod,
    url: str,
    params: dict | None = None,
    json: dict | None = None,
) -> httpx2.Response:
    """Send one HTTP request and return the successful response.

    Raises WikimediaAPIError if Wikipedia can't be reached (network errors, timeouts)
    or the response has an error status. Other httpx2 transport errors propagate
    on purpose (see ADR 0005).
    """
    try:
        response = await client.request(
            method=method, url=url, params=params, json=json
        )
    except httpx2.NetworkError as e:
        message = (
            "Couldn't reach Wikipedia (network error). "
            f"Error: {str(e) or type(e).__name__}"
        )
        raise WikimediaAPIError(message, "network") from e
    except httpx2.TimeoutException as e:
        message = (
            f"Couldn't reach Wikipedia (timeout). Error: {str(e) or type(e).__name__}"
        )
        raise WikimediaAPIError(message, "timeout") from e

    try:
        response.raise_for_status()
    except httpx2.HTTPStatusError as e:
        # defaults for parsing error
        code = str(e.response.status_code)
        message = f"Wikipedia returned HTTP {code} {e.response.reason_phrase}"

        # handle too many requests error separately, for retrying
        if e.response.status_code == 429:
            retry_after_header = e.response.headers.get("Retry-After")
            logger.info(
                "%s. Retry-After header is: %s. Response body begins: %.200s",
                message,
                retry_after_header,
                e.response.text,
            )
            retry_after = _parse_retry_after(retry_after_header)
            raise WikimediaRateLimitError(retry_after) from e

        # handle json error responses, with more specific code/message
        try:
            error_data = e.response.json()
            unexpected_format = True
            if isinstance(error_data, dict):
                code = error_data.get("errorKey") or code
                details = error_data.get("detail") or error_data.get("httpReason")
                if details:
                    message = details
                    unexpected_format = False

            if unexpected_format:
                logger.warning(
                    "%s (unexpected error format). JSON response begins: %.200s",
                    message,
                    error_data,
                )

        except ValueError:
            # for non json response log the beginning of the error body
            logger.warning(
                "%s (non-JSON response). Response body begins: %.200s",
                message,
                e.response.text,
            )

        raise WikimediaAPIError(message, code) from e
    return response


async def _delete(
    client: httpx2.AsyncClient,
    url: str,
    params: dict | None = None,
) -> httpx2.Response:
    """Send a DELETE request and return the response"""
    return await _request(client=client, method="DELETE", url=url, params=params)


async def _get(
    client: httpx2.AsyncClient, url: str, params: dict | None = None
) -> httpx2.Response:
    """Send a GET request and return the successful response.

    Wraps _request with method='GET'. Raises WikimediaAPIError as _request does.
    """
    return await _request(client=client, method="GET", url=url, params=params)


async def _post(
    client: httpx2.AsyncClient,
    url: str,
    params: dict | None = None,
    json: dict | None = None,
) -> httpx2.Response:
    """Send a POST request and return the successful response.

    Wraps _request with method='POST'. Raises WikimediaAPIError as _request does.
    """
    return await _request(
        client=client, method="POST", url=url, params=params, json=json
    )


async def _put(
    client: httpx2.AsyncClient,
    url: str,
    params: dict | None = None,
    json: dict | None = None,
) -> httpx2.Response:
    """Send a PUT request and return the successful response.

    Wraps _request with method='PUT'. Raises WikimediaAPIError as _request does.
    """
    return await _request(
        client=client, method="PUT", url=url, params=params, json=json
    )


async def opt_in(client: httpx2.AsyncClient) -> bool:
    """Opt-in the user to the Reading Lists feature.

    Returns true if the request succeeded.
    Return false if the user was already opted in.
    Otherwise, raises a WikimediaAPIError
    """

    already_set_up = "readinglists-db-error-already-set-up"
    setup_api = READING_LISTS_API + "setup"
    try:
        await _post(client, setup_api)
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
    response = await _get(client, ACTION_API, query_params)

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
        response = await _get(client, READING_LISTS_API, query_params)

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
        response = await _get(client, list_entries_api, query_params)

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
        response = await _post(client, CREATE_LIST_BATCH_API, json=batch)
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
    response = await _post(client, create_entry_batch_api, json=batch)
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
            await _delete(client, url)
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
        await _delete(client, url)
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

    response = await _put(client, url, json=json)
    return response.json()["list"]


async def _smoke_test() -> None:
    async with make_client() as client:
        username = await fetch_username(client)
        lists = await fetch_lists(client)
    # stdout carries the MCP protocol, so diagnostics go to stderr.
    print(f"Authenticated as: {username}", file=sys.stderr)
    print(f"Reading lists ({len(lists)}):", file=sys.stderr)
    for reading_list in lists:
        print(f"  {reading_list['id']}: {reading_list['name']}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(_smoke_test())
