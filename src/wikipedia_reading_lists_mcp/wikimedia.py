"""HTTP client for the Wikimedia Action API, authenticated per ADR 0001."""

import asyncio
import logging
import os
import sys
from pathlib import Path

import httpx2
from dotenv import load_dotenv

# httpx2 always appends "/" to base_url, so the base is the /w/ directory
# and requests name api.php (the Action API's single endpoint) explicitly.
API_BASE_URL = "https://en.wikipedia.org/w/"
ACTION_API = "api.php"
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


async def fetch_username(client: httpx2.AsyncClient) -> str:
    """Return the username the token authenticates as.

    Raises if the request fails or the API reports an anonymous user.
    """
    query_params = {
        "action": "query",
        "meta": "userinfo",
        "format": "json",
        "formatversion": "2",
    }
    response = await client.get(ACTION_API, params=query_params)

    response.raise_for_status()

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


async def _smoke_test() -> None:
    async with make_client() as client:
        username = await fetch_username(client)
    # stdout carries the MCP protocol, so diagnostics go to stderr.
    print(f"Authenticated as: {username}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(_smoke_test())
