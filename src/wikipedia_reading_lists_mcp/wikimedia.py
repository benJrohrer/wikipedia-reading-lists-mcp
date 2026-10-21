"""HTTP client for the Wikimedia APIs (ADR 0001 auth, ADR 0002 API choice)."""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Literal

import httpx2
from dotenv import load_dotenv

type HttpMethod = Literal["GET", "POST"]

# httpx2 always appends "/" to base_url, so the base is the domain root and
# each request names its full path. The Action API is a single endpoint; the
# reading-list REST endpoints live under the public rest_v1 prefix (ADR 0002).
API_BASE_URL = "https://en.wikipedia.org/"
ACTION_API = "w/api.php"
READING_LISTS_API = "api/rest_v1/data/lists/"
# Wikimedia's User-Agent policy asks for a client name, version, and contact.
USER_AGENT = (
    "wikipedia-reading-lists-mcp/0.1.0 "
    "(https://github.com/benJrohrer/wikipedia-reading-lists-mcp)"
)

# Resolve .env from the project root, not the working directory, because
# MCP clients may launch the server from anywhere.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


class WikimediaAPIError(Exception):
    """Raised when the Wikimedia API reports an error or returns an unexpected response."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class WikimediaAnonError(WikimediaAPIError):
    """Raised when the Wikimedia API returns an anonymous user in userinfo request."""

    def __init__(self) -> None:
        super().__init__(message="The Wikimedia API returned an anonymous user", code="anon")


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


async def _request(
    client: httpx2.AsyncClient,
    method: HttpMethod,
    url: str,
    params: dict | None = None,
    json: dict | None = None,
) -> httpx2.Response:
    """Send an HTTP request with the given method and return the successful response.

    Raises WikimediaAPIError if Wikipedia can't be reached (network errors, timeouts)
    or the response has an error status. Other httpx2 transport errors propagate
    on purpose (see ADR 0005). Should be called by helper functions which specify the method.
    """
    try:
        response = await client.request(method=method, url=url, params=params, json=json)
    except httpx2.NetworkError as e:
        message = f"Couldn't reach Wikipedia (network error). Error: {str(e) or type(e).__name__}"
        raise WikimediaAPIError(message, "network") from e
    except httpx2.TimeoutException as e:
        message = f"Couldn't reach Wikipedia (timeout). Error: {str(e) or type(e).__name__}"
        raise WikimediaAPIError(message, "timeout") from e

    try:
        response.raise_for_status()
    except httpx2.HTTPStatusError as e:
        # defaults for parsing error
        code = str(e.response.status_code)
        message = f"Wikipedia returned HTTP {code} {e.response.reason_phrase}"

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
                logging.warning("%s (unexpected error format). JSON response begins: %.200s", message, error_data)

        except ValueError:
            # for non json response log the beginning of the error body
            logging.warning("%s (non-JSON response). Response body begins: %.200s", message, e.response.text)

        raise WikimediaAPIError(message, code) from e
    return response


async def _get(
    client: httpx2.AsyncClient, url: str, params: dict | None = None
) -> httpx2.Response:
    """Send a GET request and return the successful response.

    Wraps _request with method='GET'. Raises WikimediaAPIError as _request does.
    """
    return await _request(client=client, method="GET", url=url, params=params)


async def _post(
    client: httpx2.AsyncClient, url: str, params: dict | None = None, json: dict | None = None
) -> httpx2.Response:
    """Send a POST request and return the successful response.

    Wraps _request with method='POST'. Raises WikimediaAPIError as _request does.
    """
    return await _request(client=client, method="POST", url=url, params=params, json=json)


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
        for _, value in data["warnings"].items():
            logging.warning(value["warnings"])

    if "error" in data:
        error = data["error"]
        raise WikimediaAPIError(f"{error['code']}: {error['info']}", error['code'])

    query = data["query"]
    userinfo = query["userinfo"]
    if "anon" in userinfo:
        raise WikimediaAnonError()

    return userinfo["name"]


async def fetch_lists(client: httpx2.AsyncClient) -> list[dict]:
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
            raise WikimediaAPIError(f"Received the same next token ({next_token}) twice while fetching lists.", "pagination")

        query_params["next"] = data["next"]

    return lists


async def fetch_entries(client: httpx2.AsyncClient, list_id: int) -> list[dict]:
    """Return the entries on the user's reading list with the given list_id.

    Returns every entry (all pages are fetched), each with 'id', 'project', 'title', 'created', and 'updated'.
    Raises WikimediaAPIError if the request fails. Follows `next` continuation tokens until all pages are fetched.
    """
    
    LIST_ENTRIES_API = READING_LISTS_API + f"{list_id}/entries/"
    entries = []
    query_params = {}
    while True:
        response = await _get(client, LIST_ENTRIES_API, query_params)

        data = response.json()
        entries.extend(data["entries"])

        if "next" not in data:
            break

        next_token = data["next"]
        # Guard in case we somehow get stuck in a next loop
        if query_params.get("next") == next_token:
            raise WikimediaAPIError(f"Received the same next token ({next_token}) twice while fetching entries.", "pagination")

        query_params["next"] = data["next"]

    return entries


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
