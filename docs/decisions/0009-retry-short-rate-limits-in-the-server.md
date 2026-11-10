# 0009: Retry short rate limits in the server, and pass longer ones to the model

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

Issue #11 came from Claude Desktop testing. Wikipedia returned HTTP 429 twice on 2026-11-05, and the only hint the model got was "Wait and retry." It guessed a 20-second wait, and before retrying it called `list_reading_lists` to check whether the failed call had created anything.

Wikimedia's [rate limits page](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits) gives the rules:

- **Per user, per minute.** The limit is 200 requests per minute for accounts with few edits, and 2,000 for established editors.
- **`Retry-After`.** 429 responses typically include this header. Without it, clients should wait at least 5 seconds or back off exponentially.
- **Subject to change.** The page says these limits are new in 2026.

`_request()` dropped the header, so the model never had a wait time.

The server's own traffic wasn't the cause. In Claude Desktop's log, each 429 on 2026-11-05 was the only request the server had sent in the previous minute. A test on 2026-11-09 showed that the Wikipedia app's article downloads count against the same per-user limit:

- **App closed:** a 30-article list was created without errors.
- **App open:** the server got 429s after sending about four requests in two minutes.
- **One retry 34 seconds after a 429 still failed**, because the app was still downloading.

The server can't see this traffic, so pacing its own requests wouldn't prevent these limits.

In every case:

- **The 429 came from the gateway,** with a plain-text body ("You are making too many requests to the API…").
- **It hit a GET** at the start of a tool call, before any write. A gateway 429 means the request never reached the wiki, so nothing was written.

Every tool is also safe to retry:

- **`create_list_entries`:** reports entries already on the list as duplicates.
- **`create_reading_lists`:** filters out existing names (ADR 0006).
- **`delete_list_entries`:** checks each id against the list first (ADR 0007).

The model's ability to wait depends on the client. Claude Desktop can run `sleep` in its code execution tool, which is how the model waited before. Other MCP clients may have no way to wait. The server can't tell which kind of client is calling.

MCP clients time out a tool call after a while, commonly about 60 seconds. Claude Desktop's actual value hasn't been confirmed. If a tool call times out, the model gets the client's timeout error without any of our hints.

## Options considered

### Who handles the wait

#### Option A: Report only

Raise an error with the wait time, and let the model retry or tell the user.

- Pros:
    - The simplest code, with no waiting inside the server
    - The model stays in control, and the wait is visible in the conversation
- Cons:
    - Only works well in clients where the model can wait
    - Every limit, however short, costs the model an error, a wait, and a second tool call
    - The model chooses how long to wait. Without the header it guessed.

#### Option B: The server always retries

- Pros:
    - The model never sees a rate limit
- Cons:
    - A long wait holds the tool call open past the client's timeout, and the model then gets a timeout error with no hint
    - The user isn't told why the tool is slow

#### Option C: Retry short limits, pass long ones to the model

The server waits out a 429 when `Retry-After` is short, and otherwise raises an error carrying the wait time.

- Pros:
    - Short limits cost the model nothing, in any client
    - The server waits exactly as long as `Retry-After` says
    - Long limits still reach the model, which can tell the user how long to wait
- Cons:
    - The most code of the three: a cap, an attempt limit, a time budget, and a parser for the header
    - A tool call can take up to about a minute with no visible progress
    - The thresholds are guesses until real logs show typical wait values

### How to implement the retry

#### Option D: A hand-written loop

A `for` loop in `_request()` with `asyncio.sleep`.

- Pros:
    - No dependency, and about ten lines that are easy to read
- Cons:
    - Our own code for attempt counting, time budgets, and backoff, with its own edge cases to get right

#### Option E: tenacity

- Pros:
    - A widely used, tested library
    - Separate, composable strategies: `retry=` decides whether to retry, `wait=` how long to sleep, and `stop=` when to give up. They combine with operators such as `|`, and include built-ins such as `wait_exponential`.
    - Takes a fake `sleep` through `retry_with()`, which makes the policy testable without real waiting
- Cons:
    - A new dependency
    - Its semantics have traps, and two came up here. Without `reraise=True`, the last failure raises `RetryError` instead of our error. `stop_after_delay` only counts time already passed, not the coming sleep (see "Time budget" below).

### A 429 without `Retry-After`

#### Option F: Raise immediately

- Pros:
    - Doesn't guess when the server gave no guidance
- Cons:
    - The model then has to guess instead, as on 2026-11-05

#### Option G: A fixed default wait

Wait 5 seconds, Wikimedia's stated minimum, and retry through the same path.

- Pros:
    - One code path for both cases
- Cons:
    - Doesn't adapt if the burst lasts longer than the first wait

#### Option H: Exponential backoff

Wait 5 seconds, then 10, within the same attempt limit.

- Pros:
    - Follows Wikimedia's guidance and common practice
    - Waits get longer while the burst's length is unknown
- Cons:
    - With two retries it differs from G only in the second wait

## Decision

> **Option C** (retry short limits in the server), **Option E** (tenacity), and **Option H** (exponential backoff without the header).

C won over A because it works the same in every client, uses Wikimedia's exact wait instead of a guess, and keeps short limits out of the conversation. Short limits were expected to be the common case. The one real 429 since then asked for 5 seconds. C won over B because a long wait should reach the user, rather than hang the tool until the client times out.

E won over D for a tested implementation with strategies that compose, and to learn the standard Python tool for retries.

H won over F and G because it follows Wikimedia's guidance and common practice, and the length of a burst is unknown when there's no header.

### The policy

`_request()` is decorated with tenacity's `@retry`:

| Setting | Value | Why |
|---|---|---|
| `retry=` | `retry_if_exception(_is_short_rate_limit)`: a `WikimediaRateLimitError` whose wait is `None` or at most `RATE_LIMIT_MAX_WAIT` (30 s) | A wait over 30 s is long enough that the user should know. Other errors are never retried. |
| `wait=` | `_rate_limit_wait`: the parsed `Retry-After`, or `wait_exponential(multiplier=5)` when it's missing (5 s, then 10 s) | Option H |
| `stop=` | `stop_after_attempt(3)` (`RATE_LIMIT_MAX_RETRIES + 1`) `\| stop_before_delay(55)` | Two retries are enough to ride out a short app download burst, and the time budget keeps the waits under the client's timeout (see below). |
| `reraise=True` | | Re-raises our `WikimediaRateLimitError`, which `handle_wikimedia_errors` turns into a `ToolError` with a hint. Without it, `RetryError` would get past the decorator. |
| `before_sleep=` | `before_sleep_log(logger, logging.INFO)` | Records each retry and its wait |

The request code is split in two. `_send_once()` sends one request and raises `WikimediaRateLimitError` on every 429, with or without the header. `_request()` holds the retry policy. Callers are unchanged.

`_parse_retry_after()` converts either form of the header to seconds:

- **Seconds** (`"5"`) are read as a number, with negative values clamped to 0.
- **An HTTP date** is converted to the seconds remaining, so the model never has to work out a time. A date in the past becomes 0.
- **A missing or unreadable header** becomes `None`. An unreadable one is logged.

### Time budget

The 55-second budget is meant to keep two 30-second waits under a client timeout of about 60 seconds. `stop_after_delay(55)` was tried first and never took effect: it checks the time already passed before each sleep, so waits of 30 and 30 seconds reached 60 seconds. `stop_before_delay` adds the coming sleep to that check, and stops at 30 seconds instead. Checked against a fake clock.

The budget applies to each `_request()` call, not to a tool call. A tool that sends several requests, such as `create_reading_lists` (a GET, then a POST) or `delete_list_entries` (one DELETE per entry), starts a new budget with each request. Three options were considered:

- keep 55 seconds per request and record its scope
- lower the budget so two requests fit under 60 seconds
- share one deadline across a tool call, for example through a `contextvars.ContextVar`

The first was chosen. Going over 60 seconds needs several long limits within one tool call. The second would also stop a single 30-second wait from being retried. The third would tie `wikimedia.py` to the tool layer, which ADR 0005 keeps separate.

### What the model is told

The `retry-after` hint in `ERROR_HINTS` covers every rate limit that reaches the model. It says:

- The server already retried, or the wait was too long to handle.
- The request that hit the limit had no effect, so there's no need to check whether it went through. This was the extra `list_reading_lists` call on 2026-11-05. The wording is limited to that one request, because `delete_list_entries` puts the hint in `failed` after earlier entries were already deleted (ADR 0007).
- If the model can wait, it waits the time given in the message and retries. If it can't, or no time is given, it tells the user when to try again.
- Limits are per account, so the Wikipedia app downloading articles may be the cause.

### Logging

`_send_once()` logs each 429 at INFO with the raw `Retry-After` value and the start of the response body. The body is logged as text, because the gateway's 429 isn't JSON. Parsing it as JSON raised `JSONDecodeError` in one draft and stopped every 429 from being retried.

## Consequences

- What this makes easier:
    - **Short limits are invisible to the model.** Tested 2026-11-10 with the app downloading: a 429 with `Retry-After: 5` was retried after 5 seconds, and the tool call succeeded with no error in the conversation.
    - Wikimedia's real behavior is now recorded. The header is sent in the seconds form.
    - Long limits reach the model with an exact wait in seconds, and a hint saying the request had no effect.
    - **The retry cases were checked against a fake transport and a fake clock:**
        - a short limit followed by success
        - no header (5 s, then 10 s)
        - a 120 s wait, raised immediately
        - 30 s waits stopped by the budget
        - a 404, raised immediately
- What this makes harder, or the costs we accept:
    - A tool call can wait up to about 55 seconds per request with no progress shown, and a tool that sends several requests can wait longer than that in total.
    - The thresholds (30 s, two retries, 55 s) are based on one real `Retry-After` value and an unconfirmed client timeout.
    - A new dependency, with semantics that are easy to misread.
    - The no-header path and the HTTP-date path haven't been seen in production. They have only been checked against fake responses.
    - `float()` accepts `"inf"` and `"nan"`. Neither crashes, because both fail the cap check and go to the model, but the message then shows that value.
    - No automated tests yet. The fake-clock checks were run by hand (#1).
- What would make us revisit this decision:
    - Logs show the waits Wikimedia actually sends, or 429s without the header.
    - Claude Desktop's tool-call timeout turns out to be well below 60 seconds.
    - Logs show several retries within one tool call. A deadline per tool call would then be worth its coupling.
    - Wikimedia changes its limits, or how the app's traffic counts against them.
    - MCP clients start showing progress for long tool calls, which would make longer waits acceptable.

## References

- [Issue #11: rate limits](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/11), with the log findings and test results in its comments
- [Issue #1: automated tests](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/1)
- [Wikimedia APIs: Rate limits](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits)
- [RFC 9110: Retry-After](https://www.rfc-editor.org/rfc/rfc9110#field.retry-after)
- [tenacity documentation](https://tenacity.readthedocs.io/)
- [ADR 0005](0005-convert-wikimedia-errors-to-tool-errors.md)
- [ADR 0006](0006-filter-duplicate-names-when-creating-lists.md)
- [ADR 0007](0007-delete-entries-from-one-list-with-a-pre-check.md)
