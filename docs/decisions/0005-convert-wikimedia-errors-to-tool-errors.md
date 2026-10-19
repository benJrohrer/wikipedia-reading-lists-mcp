# 0005: Convert Wikimedia errors to tool errors with next-step hints

- **Status:** Proposed
- **Date:** 2026-10-19

## Context

When a tool fails, the MCP SDK (`mcp` 2.3) only forwards the message of a
`ToolError` to the client. Any other exception becomes `UnexpectedToolError`,
and the model sees only "Error executing tool <name>". The original text stays
in the server log. We confirmed this on 2026-10-18 by calling
`list_reading_lists` with a bad token through the MCP Inspector.

This blocks the setup flow in ADR 0003, which depends on the model recognizing
`readinglists-db-error-not-set-up` and asking the user what to do.

Failures reach our code in four shapes:

- **REST errors** (4xx) with a JSON body: `errorKey`, `detail`, `httpCode`,
  `httpReason`. The `Content-Type` is `application/json`, not the
  `application/problem+json` the spec describes.
- **Gateway errors** (for example, 401 for a bad token): a JSON body with only
  `httpCode` and `httpReason`, and sometimes a more specific text (such as
  "Jwt is not in the form of Header.Payload.Signature…").
- **Responses from the edge or a proxy** that aren't JSON: HTML error pages,
  plain text (for example, the 403 for a missing User-Agent), or an empty body.
- **No response at all:** connection failures and timeouts, raised by httpx2 as
  `TransportError` subclasses.

Real bugs (`KeyError` and similar) should keep the SDK's current behavior: a
generic message for the model and a full traceback in the log.

The decision has three parts: where HTTP errors become `ToolError`, which
transport errors are handled, and where guidance for the model lives.

## Options considered

### Where the conversion to `ToolError` happens

#### Option A: `WikimediaAPIError` subclasses `ToolError`

- Pros:
    - Least code. The SDK forwards the message with no extra step.
- Cons:
    - `wikimedia.py` (the HTTP layer) would import the MCP SDK.
    - Tests for the HTTP layer (#1) would depend on MCP types.

#### Option B: `try`/`except` in each tool

- Pros:
    - Explicit and easy to read in each tool.
    - Keeps the two layers separate.
- Cons:
    - The same block in every tool. A new tool that leaves it out fails
      silently back to the generic message.

#### Option C: One decorator in `server.py`

`handle_wikimedia_errors` wraps each tool, catches only `WikimediaAPIError`,
and re-raises it as `ToolError` with `from e`.

- Pros:
    - Keeps the layers separate with no repetition.
    - Exactly one place decides what the model sees.
- Cons:
    - Each tool still needs the decorator, and in the right order: below
      `@mcp.tool()`, or the unwrapped function is registered.
    - Relies on `functools.wraps` preserving the name, docstring, and signature
      the SDK inspects. We checked this on 2026-10-19: `tools/list` output was
      unchanged, and `ctx` was still excluded from the input schema.
    - A decorator is a less familiar construct than a `try` block.

### Which transport errors are handled

#### Option D: Catch all `httpx2.TransportError`

- Pros:
    - Every failure without a response reaches the model with a reason.
- Cons:
    - Also catches our own bugs (`UnsupportedProtocol`, `LocalProtocolError`
      from a bad URL or request), and would describe them to the model as
      network problems.

#### Option E: Catch only `NetworkError` and `TimeoutException`

- Pros:
    - Covers the failures outside our control (connection errors, timeouts),
      with separate codes `"network"` and `"timeout"`.
    - Our own bugs still crash with a traceback.
- Cons:
    - Other `TransportError` subclasses also get the generic message, including
      some that aren't our bugs: `ProxyError`, and `RemoteProtocolError` (the
      server closes the connection mid-response).

#### Option F: Leave transport errors uncaught

- Pros:
    - No code.
- Cons:
    - The model can't tell a network problem from a bug in our code.

### Where guidance for the model lives

#### Option G: In the `WikimediaAPIError` message

- Pros:
    - Simple: one string, created where the error is understood.
- Cons:
    - The HTTP layer would need to know about MCP tool names (such as
      `setup_reading_lists`) and about the model as a reader.

#### Option H: In the MCP layer, keyed by `code`

`wikimedia.py` reports facts: a message and a stable `code`. `server.py` maps
codes to hints with an `ERROR_HINTS` table, plus a rule for ranges (any 5xx),
and adds the hint to the `ToolError` message.

- Pros:
    - Each layer knows only its own concerns.
    - All guidance for the model is in one readable table.
    - Hints are keyed by stable codes (Wikimedia's `errorKey`, HTTP status, or
      our own `"network"`/`"timeout"`), not by message text, which Wikimedia
      can change.
- Cons:
    - Two places to update when a new error needs guidance: the code in
      `wikimedia.py` and the hint in `server.py`.
    - A typo in a hint key fails silently (no hint). Tests in #1 should check
      the keys against codes that Layer 1 produces.

## Decision

> **Option C** (decorator), **Option E** (network errors and timeouts only), and **Option H** (hints in the MCP layer, keyed by code)

The result is two layers:

1. **`wikimedia.py`:** `_get()` turns every failed request into
   `WikimediaAPIError(message, code)` with `from e`.
    - **The code** is the `errorKey` if there is one, otherwise the HTTP status
      as a string, or `"network"`/`"timeout"`.
    - **The message** is the `detail` or `httpReason` if there is one,
      otherwise "Wikipedia returned HTTP <status> <reason>" or "Couldn't reach
      Wikipedia (...)". It is never empty.
    - **The response format** is decided from the body's fields, not from
      `Content-Type`. Error bodies that aren't JSON, or are JSON without the
      expected fields, are logged as a warning, with the first 200 characters
      of the body.
2. **`server.py`:** the decorator converts `WikimediaAPIError` to `ToolError`
   and adds a hint from `ERROR_HINTS` (or the 5xx rule) when there is one.

The decorator won over a subclass because it keeps the MCP SDK out of the HTTP
layer, and over per-tool `try` blocks because a forgotten block fails silently.
Catching only network errors and timeouts keeps our own bugs visible as
crashes. Hints are keyed by code because codes are stable, and because the HTTP
layer shouldn't know about tools or the model.

## Consequences

- What this makes easier:
    - The model now gets a reason and a next step. Verified on 2026-10-19
      through the Inspector: for a bad token (401) and for an account that
      hasn't set up reading lists.
    - The setup tool (ADR 0003) can check `e.code` for
      `readinglists-db-error-already-set-up` and treat it as success.
    - New tools get error handling by adding one decorator.
- What this makes harder, or the costs we accept:
    - Every tool must remember the decorator, in the right order.
    - The not-set-up hint currently says the server can't enable reading lists.
      It must be updated when `setup_reading_lists` is added.
    - Not covered: a successful (2xx) response that isn't JSON, for example a
      captive portal's HTML page. `response.json()` in the fetch functions would
      raise, and the model would get the generic message.
    - `ProxyError` and `RemoteProtocolError` also get the generic message (see
      Option E).
- What would make us revisit this decision:
    - The MCP SDK forwarding messages of other exception types, or supporting
      structured error data (so the code could reach the client directly).
    - Seeing `RemoteProtocolError` or proxy errors in practice.
    - Wikimedia changing its error body format. The "unexpected error format"
      warning is meant to show this.

## References

- [Issue #2: error handling](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/2)
- [Not-set-up error body](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/2#issuecomment-6254884632)
- [Already-set-up error body](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/2#issuecomment-6255577523)
- [Finding: tool exceptions don't reach the client](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/2#issuecomment-6256591200)
- [ADR 0003](0003-reading-list-setup-as-confirmed-mcp-tool.md)
- [ADR 0004](0004-share-one-http-client-through-server-lifespan.md)
- `mcp/server/mcpserver/tools/base.py`: how the SDK handles `ToolError` and
  other exceptions
