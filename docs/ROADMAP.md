# Roadmap

Status of the Wikipedia Reading Lists MCP server: what works, what's next, and
how it got here. Design decisions are recorded as ADRs in
[`docs/decisions/`](decisions/); individual work items are GitHub issues.

_Last updated: 2026-10-19_

## Current status

- **Authentication works.** An owner-only OAuth 2.0 bearer token, limited to
  English Wikipedia, authenticates both the Action API and the REST API
  ([ADR 0001](decisions/0001-authenticate-with-owner-only-oauth2.md)).
- **Reading lists can be read.** The `list_reading_lists` MCP tool returns all
  of the user's lists through the public `/api/rest_v1/data/lists/` endpoint,
  following pagination
  ([ADR 0002](decisions/0002-use-rest-api-for-reading-lists.md)).
- **The server shares one HTTP client.** The server's lifespan creates the
  client at startup and closes it on shutdown, and tools reach it through
  `AppContext`
  ([ADR 0004](decisions/0004-share-one-http-client-through-server-lifespan.md)).
- **Tool errors reach the model with a reason and a next step.** HTTP errors,
  network errors, and timeouts become `WikimediaAPIError`, and a decorator
  turns those into `ToolError` with a hint keyed by error code
  ([ADR 0005](decisions/0005-convert-wikimedia-errors-to-tool-errors.md)).
  Checked through the Inspector with a bad token and with an account that
  hasn't set up reading lists.
- **Not yet:** no write tools, no setup tool, and no automated tests.

## Next up

1. **Setup tool**
   ([ADR 0003](decisions/0003-reading-list-setup-as-confirmed-mcp-tool.md)):
   `setup_reading_lists`, called only after the user agrees. Treat
   `readinglists-db-error-already-set-up` as success, and update the
   not-set-up hint in `ERROR_HINTS`, which currently says the server can't do
   this. Includes testing whether `ctx.elicit()` can ask the user directly.
2. **Automated tests with mocked Wikimedia responses**
   ([#1](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/1)):
   start with the error cases from #2 (mocked HTTP error bodies, transport
   errors, and the hint lookup), which have so far only been checked by hand.

## Later

- Linting and formatting with ruff
  ([#3](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/3)).
- Handle successful responses that aren't JSON
  ([#4](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/4)).
- Write tools: create lists, add and remove entries (through the `/batch`
  endpoints).

## Progress log

Newest first.

### 2026-10-19

- Added this roadmap and an end-of-day routine for updating it, and documented
  how to pass environment variables to the server through the Inspector.
- Finished error handling in two layers
  ([#2](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/2),
  [ADR 0005](decisions/0005-convert-wikimedia-errors-to-tool-errors.md)):
  - `wikimedia.py` turns every failed request into
    `WikimediaAPIError(message, code)`.
  - A decorator in `server.py` converts it to `ToolError`, the only exception
    type whose message the MCP SDK forwards.
- Chose to catch only network errors and timeouts, so that errors caused by our
  own code still crash with a traceback, and to keep hints for the model in the
  MCP layer, keyed by stable codes, not by message text.
- Learned that Wikimedia's error bodies come in more shapes than expected:
  - plain-text and HTML pages from the CDN
  - a gateway 401 with JWT-specific text
  - none of them sent as `application/problem+json`

  So the parser decides the format from the body's fields, never from
  `Content-Type`, and logs any format it doesn't recognize.
- Confirmed through the Inspector that the not-set-up error reaches the model
  with its hint, against a live account. This is the step ADR 0003's setup
  flow depends on.
- Recorded the shared-client decision
  ([ADR 0004](decisions/0004-share-one-http-client-through-server-lifespan.md)).
- Opened [#4](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/4)
  for successful responses that aren't JSON, a gap noted in ADR 0005.

### 2026-10-18

- Moved reading-list calls to the REST API after finding that the Action API's
  ReadingLists modules are marked "internal or unstable, and you should not use
  it"; chose the public `rest_v1` URL that Wikimedia kept stable
  ([ADR 0002](decisions/0002-use-rest-api-for-reading-lists.md)).
- Added `fetch_lists()` with pagination and a guard against a repeated
  continuation token.
- Confirmed by experiment that bearer-token writes need no CSRF token, and that
  calling setup on an already set-up account changes nothing.
- Decided that reading-list setup is an MCP tool the model calls only after
  asking the user, and that teardown is not exposed
  ([ADR 0003](decisions/0003-reading-list-setup-as-confirmed-mcp-tool.md)).
- Replaced the demo server with a lifespan-managed client and the first real
  tool, `list_reading_lists`.
- Opened issues #2 (error handling) and #3 (linting).
- Found that the MCP SDK passes only `ToolError` messages to the client, which
  expanded the scope of #2.

### 2026-10-17

- Added `httpx2` and `python-dotenv`, and a smoke test that authenticates and
  reports the username through `meta=userinfo`.
- Traced three error layers: the auth gateway (HTTP 401), Action API errors
  (HTTP 200 with an `error` body), and responses that succeed but are
  anonymous.
- Opened issue #1 for an automated test suite.

### 2026-10-16

- Chose owner-only OAuth 2.0 over OAuth 1.0a, bot passwords, and a full OAuth
  flow ([ADR 0001](decisions/0001-authenticate-with-owner-only-oauth2.md)).
- Registered the consumer (English Wikipedia only, basic rights and "access
  private information") and added `.env.example`.
- Added CLAUDE.md and the ADR template.

### 2026-10-15

- Started the project with a basic test MCP server.
