"""HTTP client for the Wikimedia APIs (ADR 0001 auth).

Sends every request, turns failed responses into WikimediaAPIError, and waits
out short rate limits (ADR 0009).
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal

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

from wikipedia_reading_lists_mcp.errors import (
    WikimediaAPIError,
    WikimediaRateLimitError,
)

type HttpMethod = Literal["GET", "POST", "DELETE", "PUT"]

# httpx2 always appends "/" to base_url, so the base is the domain root and
# each request names its full path.
API_BASE_URL = "https://en.wikipedia.org/"
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


async def delete(
    client: httpx2.AsyncClient,
    url: str,
    params: dict | None = None,
) -> httpx2.Response:
    """Send a DELETE request and return the response"""
    return await _request(client=client, method="DELETE", url=url, params=params)


async def get(
    client: httpx2.AsyncClient, url: str, params: dict | None = None
) -> httpx2.Response:
    """Send a GET request and return the successful response.

    Wraps _request with method='GET'. Raises WikimediaAPIError as _request does.
    """
    return await _request(client=client, method="GET", url=url, params=params)


async def post(
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


async def put(
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
