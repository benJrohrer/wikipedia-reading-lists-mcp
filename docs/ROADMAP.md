# Roadmap

Status of the Wikipedia Reading Lists MCP server: what works, what's next, and
how it got here. Design decisions are recorded as ADRs in
[`docs/decisions/`](decisions/); individual work items are GitHub issues.

_Last updated: 2026-10-20_

## Current status

- **Authentication works.** An owner-only OAuth 2.0 bearer token, limited to
  English Wikipedia, authenticates both the Action API and the REST API
  ([ADR 0001](decisions/0001-authenticate-with-owner-only-oauth2.md)).
- **Reading lists and their entries can be read.** `list_reading_lists` returns
  all of the user's lists through the public `/api/rest_v1/data/lists/`
  endpoint, and `list_entries` returns every article on one list. Both follow
  pagination ([ADR 0002](decisions/0002-use-rest-api-for-reading-lists.md)).
- **Users can opt in to Reading Lists.** `setup_reading_lists` enables the
  feature, and Claude calls it only after the user agrees. An account that is
  already set up counts as success
  ([ADR 0003](decisions/0003-reading-list-setup-as-confirmed-mcp-tool.md)).
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
- **All requests go through one function.** `_request()` sends any HTTP method
  and handles errors in one place; `_get()` and `_post()` set the method, and a
  `Literal` type limits which methods can be passed
  ([#5](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/5)).
  Request bodies are sent as JSON.
- **Linting and formatting are automatic.** Ruff checks and formats the code
  through a pre-commit hook that runs the ruff version pinned in `uv.lock`.
  Settings live in `pyproject.toml`; the reasons for them are in
  [#3](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/3).
- **The README has a first draft.**
- **Not yet:** no tools that create lists or add entries, and no automated
  tests.

## Next up

1. **Create a list**: a tool for `POST /data/lists/`. The first request that
   sends a body, so it also confirms that the endpoint accepts JSON (the spec
   gives the content type as `*/*`).
2. **Add entries to a list**: a tool for `POST /data/lists/{id}/entries/`.
   Still open: whether to add one entry per call or use the `/batch` endpoints
   (`/data/lists/batch`, `/data/lists/{id}/entries/batch`), which would let a
   whole reading list be saved in one call.
3. **Open issues:**
   - Automated tests with mocked Wikimedia responses
     ([#1](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/1)).
     Start with the error cases (mocked HTTP error bodies, transport errors,
     and the hint lookup), which have so far only been checked by hand.
   - Handle successful responses that aren't JSON
     ([#4](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/4)).

## Later

- **Find which lists contain an article**: a tool for
  `GET /data/lists/pages/{project}/{title}`, which returns the user's lists
  that include that article.
- **Maybe: delete tools** for whole lists (`DELETE /data/lists/{id}`) and
  single entries (`DELETE /data/lists/{id}/entries/{entry_id}`). Not decided
  yet. These are the first destructive tools, so they would need a decision
  about confirmation, like the one in ADR 0003.

## Progress log

Newest first.

### 2026-10-20

- Added `setup_reading_lists`, which opts the user in to Reading Lists, and
  accepted [ADR 0003](decisions/0003-reading-list-setup-as-confirmed-mcp-tool.md).
  The tool is annotated as non-destructive and idempotent, because opting in
  writes data but deletes none. Consent comes from the docstring and the
  not-set-up hint, not from the annotation.
- Added `list_entries`, which returns every article on a list, following
  pagination.
- Fixed `list_entries` types, the `opt_in` return value, and tool annotations.
- Moved `main()` into `server.py` as the console-script entry point.
- Planned the write tools: create a list and add entries next, with a
  find-lists-by-article lookup and possible delete tools later.
- Opened [#5](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/5)
  to combine the duplicated request code in `_get()` and `_write()`, and
  scheduled it before the write tools so request bodies are handled in one
  place.
- Accepted [ADR 0004](decisions/0004-share-one-http-client-through-server-lifespan.md)
  and [ADR 0005](decisions/0005-convert-wikimedia-errors-to-tool-errors.md),
  and wrote a first draft of the README.
- Finished [#5](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/5):
  - All requests now go through `_request()`, using httpx2's
    `client.request()`, with small `_get()` and `_post()` helpers.
  - `_write()` became `_post()`, and bodies switched from form encoding to JSON
    because the write endpoints take nested objects.
  - A `Literal` type for the HTTP method lets a type checker catch a misspelled
    method before the code runs.
- Finished [#3](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/3),
  adding ruff with the default rules plus `E`, `W`, `I`, `UP`, `B`, and `SIM`,
  an 88-character line limit, and `ruff format`:
  - The pre-commit hook is a local hook running `uv run ruff`, so the hook,
    the editor, and the terminal all use the version pinned in `uv.lock`.
  - Chose `extend-select` so ruff's own default rules come along as they
    change. Upgrading from 0.14 to 0.17 had already added `LOG` and `PERF`
    findings.
  - Applied the formatter in its own commit and listed it in
    `.git-blame-ignore-revs`, so blame skips it. Then fixed the remaining
    findings: long lines, a module-level `logger` in place of root-logger
    calls, and `.values()` in place of `.items()`.
- Decided that development-tooling choices are recorded in their issue rather
  than in an ADR. ADRs are kept for decisions about the server itself.

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
