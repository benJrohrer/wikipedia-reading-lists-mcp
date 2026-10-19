# 0004: Share one HTTP client through the server lifespan

- **Status:** Proposed
- **Date:** 2026-10-19 (decision implemented 2026-10-18 in `8b7afcb`)

## Context

Every tool calls the Wikimedia APIs through an `httpx2.AsyncClient` that
carries the bearer token and User-Agent (ADR 0001). The client keeps a pool of
connections, so it should be reused across requests and closed when it's no
longer needed. If it's never closed, its connections stay open and httpx2 warns
about it.

Tool functions are called by the MCP SDK, not by our code, so we can't pass the
client to them as an argument. The SDK provides two hooks for shared state:
a `lifespan` async context manager that runs at server startup and shutdown,
and `ctx.request_context.lifespan_context`, which gives each tool call the
object the lifespan yielded.

The question is when the client is created and closed, and how tools reach it.

## Options considered

### Option A: A new client for each tool call

Each tool opens `async with make_client() as client:` around its own requests.

- Pros:
    - Simplest code: no shared state, nothing to wire up.
    - The token is read on every call, so a corrected token in `.env` works
      without restarting the server.
- Cons:
    - No connection reuse between tool calls: each call pays for a new TLS
      connection. (Pagination within one call still reuses the connection.)
    - Repeats the client setup in every tool.

### Option B: A module-level client created at import

`client = make_client()` at the top of `server.py`, used directly by tools.

- Pros:
    - Short. Tools reach the client without going through `ctx`.
- Cons:
    - Nothing closes it. There is no natural place to call `aclose()`.
    - Importing the module requires a valid token, which affects tests (#1)
      and tools such as `mcp dev` that import the server.
    - The client is created before the event loop runs, which async clients
      don't always handle well.

### Option C: Create the client in the server lifespan and expose it through `AppContext`

`lifespan()` opens the client with `async with make_client()`, yields an
`AppContext(client)` dataclass, and closes the client on shutdown. Tools read
it from `ctx.request_context.lifespan_context`.

- Pros:
    - One client for the server's lifetime, so connections are reused across
      tool calls.
    - Closing is guaranteed by `async with`, including on errors at shutdown.
    - Uses the SDK's intended mechanism. `AppContext` is typed and gives future
      shared state (for example, a cached username) an obvious home.
    - A missing token fails at startup (`get_access_token()` raises), not on the
      first tool call.
- Cons:
    - Each tool needs the `ctx: Context` parameter and the
      `ctx.request_context.lifespan_context` line.
    - The token is read once. After changing it, the user has to restart the
      server.
    - A bad token (as opposed to a missing one) isn't detected at startup, only
      when a tool calls the API.

## Decision

> **Option C**: create the client in the server lifespan and expose it through `AppContext`

It's the only option that both reuses connections across calls and closes the
client reliably, and it's the mechanism the MCP SDK provides for this. The
extra `ctx` parameter in each tool is a small, repeated cost, and the restart
requirement is acceptable for a server that one person configures once.

## Consequences

- What this makes easier:
    - Adding tools: each one gets the configured client from `ctx` with one line.
    - Adding other shared state later: it goes in `AppContext`.
    - Testing (#1): a test can build an `AppContext` around a client with a
      mock transport.
- What this makes harder, or the costs we accept:
    - Changing the token requires a server restart. The 401 hint from ADR 0005
      tells the model this, so it can pass it on to the user.
    - Tools depend on the SDK's `Context` type, not only on a client argument.
- What would make us revisit this decision:
    - The server needing more than one account or token (one client per user).
    - The MCP SDK changing how lifespan state reaches tools.

## References

- [MCP Python SDK: lifespan and context](https://github.com/modelcontextprotocol/python-sdk)
- [ADR 0001](0001-authenticate-with-owner-only-oauth2.md)
- [ADR 0005](0005-convert-wikimedia-errors-to-tool-errors.md)
- Commit `8b7afcb`: add MCP server lifespan and list_reading_lists tool
