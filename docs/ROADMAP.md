# Roadmap

Status of the Wikipedia Reading Lists MCP server: what works, what's next, and
how it got here. Design decisions are recorded as ADRs in
[`docs/decisions/`](decisions/); individual work items are GitHub issues.

_Last updated: 2026-10-25_

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
  Server instructions also tell the model to make sure the user is opted in
  before creating lists.
- **Reading lists can be created.** `create_reading_lists` takes one or more
  lists (name and optional description) and posts them to
  `/data/lists/batch`. The API overwrites the description of a list that
  already has the requested name, so the tool fetches the existing lists first
  and leaves those names out. It returns `created`, `excluded` (the existing
  lists, with their ids), and `updated` (a backstop for the rare case where a
  list was still overwritten)
  ([ADR 0006](decisions/0006-filter-duplicate-names-when-creating-lists.md)).
- **Articles can be added to a list.** `create_list_entries` takes a list id
  and one or more entries (project and title) and posts them to
  `/data/lists/{id}/entries/batch`. An article already on the list is left
  unchanged and comes back with `"duplicate": true`, so no filtering is
  needed. The batch isn't atomic: if one entry fails, the entries before it
  are saved and the rest are not. The tool's docstring says so, and the hint
  for an unknown project tells the model to fix the project and resend the
  whole batch.
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
  In VS Code, ruff also formats on save, using the same pinned version.
- **The README covers** what the server does, how it works, setup, and the
  design decisions.
- **Not yet:** no way to remove articles or lists, and no automated tests.

## Next up

1. **Decide how to guide the size of new lists**
   ([#6](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/6)).
   In testing, a request for a list about animal taxonomy produced 70 entries.
   The options are soft guidance, suggesting several themed lists, an env-var
   setting, a hard cap, or an MCP prompt. The decision gets an ADR.
2. **Open question:** the server instructions overlap with the not-set-up
   hint and may make the model ask about opting in more often than needed.
   Decide whether to keep them, and update ADR 0003 if so.
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
  about confirmation, like the one in ADR 0003. They would also let the model
  split an oversized list into smaller ones
  ([#6](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/6)).
- **Maybe: a tool to change a list's description**, if users want it. ADR 0006
  prefers a separate tool over an overwrite option on create.
- **Maybe: elicitation** on a name conflict, on top of the filter, for clients
  that support it. Deferred in ADR 0006.
- **A type checker** (pyright or mypy). `ListSpec`, `EntrySpec`, and the
  result types are `TypedDict`s, which only a type checker checks inside our
  own code. A tooling choice, so it would go in an issue.

## Progress log

Newest first.

### 2026-10-25

- Added `create_list_entries`, which adds one or more articles to a list:
  - Tested in Claude Desktop: an article already on the list isn't changed
    and comes back with `"duplicate": true`. Unlike list creation
    ([ADR 0006](decisions/0006-filter-duplicate-names-when-creating-lists.md)),
    nothing is overwritten, so the tool doesn't fetch existing entries first.
    Titles with spaces and with underscores count as the same article.
  - Every entry in the response has a `duplicate` field, so
    `CreateEntryResult` declares it as required. The SDK builds the tool's
    output schema from that type.
  - An empty `entries` list returns `[]` without sending a request, like
    `create_lists`.
  - The docstring tells the model to use the decoded title, with no
    `#fragment`, and where list ids come from.
- Learned that the entries batch isn't atomic. An unknown project returns
  `readinglists-db-error-no-such-project`, and the entries before the bad one
  are saved while the bad one and the rest are not. Confirmed by testing
  both halves.
- Added a hint for that error. It tells the model to fix the project and
  resend the whole batch, which is safe because repeating a create changes
  nothing. On the retry, the model ignores `duplicate` only for the entries
  before the bad one, because those were saved by the failed call.
- Found the error's code by calling `create_entries` directly: the ToolError
  message carries only the API's `detail` text, not its `errorKey`.
- Opened [#6](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/6)
  after a test request produced a 70-entry list.

### 2026-10-24

- Added `create_reading_lists` and accepted
  [ADR 0006](decisions/0006-filter-duplicate-names-when-creating-lists.md):
  - Tested on 2026-10-22: the batch endpoint doesn't reject a name that already
    exists. It overwrites that list's description, or clears it if none is
    sent, and returns `"duplicate": true`. Name matching is case-sensitive.
  - Chose to check existing names on the server and leave them out, returning
    them as `excluded`, over documenting the overwrite, an `overwrite`
    parameter, or elicitation. The docstring also asks the model to check
    `list_reading_lists` first, so it can raise similar names with the user.
  - With the filter, `destructive_hint=False` and `idempotent_hint=True` are
    accurate, apart from a short race between the check and the POST.
  - An empty `batch` returns HTTP 400, so no request is sent when every name
    was filtered out. A name repeated within one request keeps its last copy.
- Merged the planned single and batch create tools into one tool that takes a
  list, because the API only has a batch endpoint and two overlapping tools
  made the model choose between them.
- Replaced `list[tuple[str, str]]` with `TypedDict`s (`ListSpec`,
  `CreateListsResult`), so the tool's input and output schemas name their
  fields. Chose TypedDict over Pydantic because the items are plain dicts sent
  straight to the API.
- Added server instructions about opting in before creating lists.
- Learned that the API sandbox can't send this request: it sends the spec's
  `*/*` as `Content-Type` and gets HTTP 415. Our client sends
  `application/json`, which settles the earlier question of whether the
  endpoint accepts JSON.
- Set up format on save with ruff in VS Code, kept out of the repo; noted in
  [#3](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/3#issuecomment-6393486868).
  Formatting before staging avoids pre-commit failures on partial commits.

### 2026-10-21

- Added the MIT License, accepted ADRs 0001 and 0002, and edited the README.

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
