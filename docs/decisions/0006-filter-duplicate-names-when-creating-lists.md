# 0006: Filter duplicate names when creating reading lists

- **Status:** Accepted
- **Date:** 2026-10-24

## Context

Reading lists are created through POST /data/lists/batch, since posting to /data/lists/ is deprecated ([ADR 0002](0002-use-rest-api-for-reading-lists.md)). The endpoint takes `{"batch": [{"name": ..., "description": ...}]}` and returns the resulting lists under `lists`.

The endpoint does not reject a name that already exists. Tested 2026-10-22 against an existing list: the request succeeded, no second list was created, the existing list's description was replaced with the one sent, its `updated` timestamp changed, and the response included `"duplicate": true`. In effect the endpoint creates or updates. Sending an existing name with no description also updates the list, and clears its description (tested 2026-10-22). Name matching is case-sensitive, so "rome" and "Rome" are different lists (verified by test 2026-10-24).

This matters because a "create" tool can then overwrite a description the user wrote, with nothing in the conversation showing it happened. It also decides the tool annotations: MCP defines `destructiveHint: false` as "the tool performs only additive updates," which an overwrite is not.

Two more API behaviors shape the design:

- An empty `batch` is rejected with HTTP 400: "must contain an array of objects which have scalar values." Found when every list in a request had been filtered out as a duplicate.
- The same name twice in one batch isn't covered by a check against existing lists, so it has to be handled within the batch. Tested 2026-10-24: the last copy was kept.

The expected use is conversational ("save what we've been talking about to a reading list"), so the model should be able to suggest an existing list with a similar name, not only catch exact matches.

## Options considered

### Option A: Accept the upsert and document it

Pass names straight to the API, mark the tool `destructive_hint=True`, and tell the model in the docstring that an existing name overwrites that list's description.

- Pros:
    - Simplest code, one request per call
    - The annotations accurately describe what the tool does
- Cons:
    - Protecting the user's data depends entirely on the model following the docstring
    - A destructive hint may make clients ask for confirmation on every create, even though most creates are new lists

### Option B: Check existing names before creating

Before posting, fetch the user's lists, remove any requested names that already exist, and return them separately so the model can ask the user what to do.

- Pros:
    - The server never sends an existing name, so a description can't be overwritten through this tool (except in the race described under Consequences)
    - `destructive_hint=False` becomes accurate
    - Returns the existing lists, with their ids, so the model can offer to use them instead
- Cons:
    - An extra paginated GET on every call
    - A gap between the GET and the POST, so this narrows the overwrite risk rather than removing it

### Option C: Check first, then send no description for existing names

Like B, but existing names are sent without a description, in the hope that the API returns the existing list unchanged.

- Pros:
    - The model would get existing lists back from the same endpoint
- Cons:
    - Doesn't work: tested 2026-10-22, a missing description clears the existing one, which is worse than overwriting it
    - Would depend on undocumented API behavior even if it worked
    - Still updates the `updated` timestamp

### Option D: Check first, with an `overwrite` parameter

Like B, but overwrite existing lists when the model passes `overwrite=True` after the user agrees.

- Pros:
    - Supports the case where the user really does want to change a description
- Cons:
    - Adds a parameter whose safe use depends on the model, the same weakness as A, now opt-in
    - Changing a description is a different operation from creating a list, and would be clearer as its own tool if needed

### Option E: Elicitation on a conflict

When the server finds an existing name, pause the tool call and ask the user through an MCP elicitation form (overwrite, rename, or skip).

- Pros:
    - The user is asked even if the model skipped the check
- Cons:
    - Client support for elicitation varies, so a fallback such as B is needed anyway
    - The MCP SDK notes that an agent client may answer an elicitation itself, so it doesn't guarantee a human answer
    - Only catches exact matches; similar names still need the model's judgment
    - A form in the middle of a conversation is more disruptive than the model asking

## Decision

> **Option B**: check existing names before creating, plus a docstring nudge for the model to check first.

The design has three layers:

1. **Docstring:** the `create_reading_lists` docstring tells the model to call `list_reading_lists` before creating, so it can raise exact and similar names with the user in the conversation. This is the flexible layer; it relies on the model.
2. **Server-side filter:** `create_lists` fetches the existing lists and leaves out any requested name that already exists. A name repeated within one request is reduced to its last copy. If nothing is left to create, no POST is sent. This is the layer that doesn't rely on the model.
3. **Three-part result:** `created` (new lists), `excluded` (existing lists with the requested names, including their ids), and `updated` (lists the API still reported as `duplicate`, the race backstop). The docstring tells the model to tell the user about `excluded` and `updated` entries and to ask how to proceed.

B won because it keeps users' descriptions safe without depending on the model, keeps the annotations accurate, and still leaves the conversational flexibility to the model. Elicitation (E) is deferred, not rejected: it could be added later on top of B for clients that support it.

### One tool instead of two

An earlier version had `create_new_list` and `create_new_list_batch`. They were merged into one `create_reading_lists` tool that takes a list of one or more items. The batch tool already covered the single case, and the API only offers a batch endpoint. Two overlapping tools made the model choose between them and needed docstrings that pointed at each other. With the filter, both would also have needed the same three-part result. Models handle one-item arrays well. If the model passes a bare object instead, the validation error goes back to it and it can retry.

### TypedDict for input and output

The input is `list[ListSpec]` and the result is `CreateListsResult`, both `TypedDict`s in `wikimedia.py`, replacing `list[tuple[str, str]]`.

- **Why not a tuple:** the tuple produced an input schema of unnamed strings, with only the docstring explaining which was the name.
- **Why TypedDict over Pydantic:** a TypedDict is a plain dict at runtime, so the items can be sent to the API as they are and `wikimedia.py` doesn't depend on Pydantic. The MCP SDK still validates the input against the schema at the tool boundary.
- **Cost:** inside our own code nothing checks these types at runtime, and the project has no type checker yet.

## Consequences

- What this makes easier:
    - Creating a list can't overwrite an existing description through normal use.
    - `destructive_hint=False` and `idempotent_hint=True` describe the tool accurately. Repeating a call with the same names leaves them all in `excluded` and sends no POST, so nothing changes, not even `updated`.
    - The model gets the ids of existing lists in `excluded`, so it can move on to using those lists (for example, adding entries once that tool exists).
- What this makes harder, or the costs we accept:
    - Every create call first fetches all of the user's lists, which may take several requests.
    - **Race window:** a list with the same name created between the GET and the POST (for example, from the Wikipedia app) still gets its description overwritten. We accept this because it requires two clients to create the same name within moments. If the request had no description, the existing description is cleared rather than replaced. The `duplicate` flag puts such lists in `updated`, so the model can tell the user, and the server logs a warning with the list's name and id. In that case `destructive_hint=False` is not strictly true.
    - If a name appears twice in one request, the earlier copies are dropped without being reported.
    - The filter compares names exactly, matching the API's case-sensitive behavior. If the API's matching changes, for example to case-insensitive, duplicates would get past the filter. They would then show up in `updated` rather than `excluded`.
- What would make us revisit this decision:
    - Users really want to change descriptions: add a separate update tool instead of an overwrite option.
    - The number of lists makes the extra GET noticeably slow.
    - Lists appear in `updated` in practice (seen as warnings in the server log), meaning the filter and the API disagree.
    - Elicitation becomes widely supported by clients and reliably reaches a person.
    - The API starts rejecting duplicate names itself, which would make the filter unnecessary.

## References

- [rest_v1 spec](https://en.wikipedia.org/api/rest_v1/?spec)
- [MCP spec: tool annotations](https://modelcontextprotocol.io/specification/2025-06-18/schema#toolannotations)
- [MCP spec: elicitation](https://modelcontextprotocol.io/specification/2025-06-18/client/elicitation)
- [ADR 0002](0002-use-rest-api-for-reading-lists.md)
- [ADR 0003](0003-reading-list-setup-as-confirmed-mcp-tool.md)
- [ADR 0005](0005-convert-wikimedia-errors-to-tool-errors.md)
