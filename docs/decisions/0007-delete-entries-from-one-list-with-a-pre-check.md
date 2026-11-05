# 0007: Delete entries from one list at a time, with a pre-check

- **Status:** Accepted
- **Date:** 2026-11-05

## Context

Issue #7 adds a tool to remove articles from a reading list, using `DELETE /data/lists/{id}/entries/{entry_id}`. It is the server's first destructive tool. The endpoint removes one entry per request, and there is no batch endpoint, so removing several articles takes several requests. Splitting or trimming an oversized list (#6) is an expected use, so removing many entries at once needs to be practical.

The endpoint doesn't reliably check the list in its URL. A DELETE with a valid entry id and an invalid list id succeeded, and the entry was deleted. This matters because a model working across several lists could pair an entry with the wrong list, and the API would still delete it. The API can't be relied on to catch that mistake. Tested on 2026-10-30 against an invalid list_id and another real list_id belonging to user.

The issue left four questions open: whether the tool takes ids or titles, whether it takes one entry or many, how much confirmation it needs, and what happens when an entry is already gone. Delete-list (#8) needs its own, stronger safeguards, and is expected to build on the confirmation approach recorded here.

Our usual error path (ADR 0005) raises `ToolError`, which tells the model that the call failed. That doesn't fit a batch where some deletes have already happened.

## Options considered

### Shape of the input

#### Option A: A batch of `list_id`/`entry_id` pairs

One call can touch several lists. This was the first implementation.

- Pros:
    - One call covers any mix of lists
- Cons:
    - Each list gets its own lookup, so a failure can happen at any point in the loop, between deletes
    - Every error has to be classified as belonging to one entry or to the whole call
    - The shape differs from `create_list_entries`, which takes one `list_id`

#### Option B: One pair per call

- Pros:
    - Matches the endpoint
    - Every error takes the normal `ToolError` path, with no partial results to report
    - In clients that prompt for each tool call, each delete gets its own approval
- Cons:
    - Removing N entries means N calls, and N fetches of the whole list to check membership. This is worst for the oversized lists in #6.
    - Many approval prompts for one request from the user
    - The only tool here that doesn't accept several items

#### Option C: One `list_id` and a list of `entry_ids`

- Pros:
    - One fetch per call, however many entries are removed
    - Failures split along a clear line: the fetch happens before any delete, so a fetch failure can fail the whole call with nothing deleted
    - Matches the shape of `create_list_entries`
- Cons:
    - Needs a partial-failure result, because a DELETE can still fail partway through
    - Several lists need several calls
    - In clients that prompt per call, one approval can cover many deletes

### Checking that an entry is on the list

#### Option D: Trust the DELETE endpoint

- Pros:
    - No extra requests
- Cons:
    - Deletes the entry even when `list_id` is wrong, as tested above

#### Option E: Fetch the list first and only delete ids found on it

- Pros:
    - Enforces the `list_id` the model passed, which the API doesn't
    - Doesn't rely on the model
- Cons:
    - An extra paginated GET on every call
    - Can only see what is on the list now, so it can't tell why an id is missing

### When a DELETE fails partway through

#### Option F: Keep going and record each failure

- Pros:
    - Every entry gets attempted
- Cons:
    - After a 401, a 429, or a network failure, the remaining requests will probably fail too, and continuing adds to a rate limit
    - Can produce many identical failures

#### Option G: Raise `ToolError` at the first failure

- Pros:
    - Uses the existing error path
- Cons:
    - Loses the record of what was already deleted

#### Option H: Stop at the first failure and return a partial result

- Pros:
    - Keeps the record of completed deletes
    - Stops sending requests to a service that is failing
    - Tells the model exactly which ids are left
- Cons:
    - Stops the batch for an error that might only have affected one entry
    - The tool has to attach the hint itself, because the decorator only handles raised errors

### Reporting entries that are already gone

#### Option I: A separate `already_deleted` bucket

- Pros:
    - Keeps the race case distinct
- Cons:
    - Misleading. The pre-check runs first, so an entry deleted in an earlier call looks like any other id not on the list. The bucket only fills if an entry disappears between the fetch and its DELETE. In a Claude Desktop test, previously deleted ids came back as `excluded`, and the model read that as a mismatch with the docstring.

#### Option J: Report them in `excluded`

- Pros:
    - One bucket with one meaning: not on this list, nothing to do
    - Doesn't promise a distinction the tool can't make
- Cons:
    - The model can't tell an entry on another list from one already removed

#### Option K: Tell the two cases apart in code

- Pros:
    - The most informative result
- Cons:
    - Probing with a DELETE would remove an entry that belongs to another list, which is what the pre-check prevents
    - Any other route needs history the list's entries don't provide, and adds requests for a label that wouldn't change what the model does next

## Decision

> **Option C** (one list, many entry ids), **Option E** (pre-check), **Option H** (stop and return a partial result), and **Option J** (report gone entries as excluded)

`delete_entries` fetches the list once, outside any `try`, so a failed fetch raises and nothing is deleted. It removes repeated ids, keeping their order, then handles each one:

- **Not on the list:** goes in `excluded`, and no DELETE is sent.
- **DELETE succeeds:** goes in `deleted`.
- **DELETE reports the entry is already deleted:** goes in `excluded`, and the loop continues.
- **Any other error:** recorded in `failed`, the remaining ids go in `not_attempted`, and the loop stops.

C won over B because #6 needs efficient removal of many entries, and over A because limiting a call to one list leaves the fetch as the only whole-call failure. E is required by the API's behavior. H won because it is the only option that keeps the record of completed deletes and also stops calling a failing service. J won because the tool can't reliably know more than "not on this list."

### Why any other error stops the batch

The pre-check has already filtered out the entries a per-entry error would be about. An unexpected error is therefore more likely to be about the token, the rate limit, or the connection. For a destructive tool, stopping is also the cautious default for an error we don't understand, so there is no list of errors that are safe to continue past.

### Shape of `failed`

`failed` is a single optional `FailedDelete` record (`entry_id`, `code`, `message`, and sometimes `hint`), not a list, because the loop stops at the first failure. The record holds plain data, not `WikimediaAPIError` objects. Tested 2026-11-05: with an exception type in the return annotation, the SDK logged a schema warning, the tool had no output schema, and the value was reduced to its message, which lost `code`.

The failed entry stays out of `not_attempted`, because after a timeout the DELETE may have gone through. Retrying it is safe: if it was deleted, the pre-check reports it as `excluded`.

### Hints in partial results

This extends ADR 0005. `ToolError` still means the call did nothing. When a batch stops partway, `delete_list_entries` looks up the hint with `_error_hint` and adds it to `failed`. `wikimedia.py` keeps reporting only facts, and `ERROR_HINTS` stays the one place for guidance.

### Ids instead of titles

The tool takes entry ids from `list_entries` or `create_list_entries`, not project and title. An id identifies exactly one entry, while a title lookup would depend on the model's title matching the stored form.

### Confirmation

Removing entries uses `destructive_hint=True` and a docstring instruction to get the user's explicit permission first, the same model-reliant approach as ADR 0003. This tool adds no further safeguards, because one call affects a limited set of named entries and an article can be added back with `create_list_entries`. Delete-list (#8) removes a whole list at once and has further safeguards planned there.

## Consequences

- What this makes easier:
    - A model that pairs an entry with the wrong list can't delete it through this tool. Tested 2026-11-05 through Claude Desktop: an id from one list sent with another came back in `excluded`, and the entry's list kept all of its articles.
    - `idempotent_hint=True` is supported: repeating a call in that same test left the deleted ids in `excluded` and raised no error.
    - After a failure, the model has an exact list of ids to retry.
    - Removing many entries from one list costs one fetch plus one DELETE per entry.
- What this makes harder, or the costs we accept:
    - Every call makes an extra paginated GET, even for one entry.
    - `excluded` can't say why an id isn't on the list.
    - One error stops the batch, even if the remaining entries would have succeeded.
    - **Untested:** the pre-check stops repeat deletes before they reach the API, so `readinglists-db-error-list-entry-deleted` hasn't been seen from a real DELETE. If the API returns a different code for the race, the batch stops and reports `failed`. Nothing is wrongly deleted, and a retry reports the entry as `excluded`.
    - Errors other than `WikimediaAPIError` still propagate (ADR 0005). `RemoteProtocolError` or `ProxyError` partway through a batch would lose the record of completed deletes.
    - Confirmation relies on a cooperative model and client, as in ADR 0003. In clients that prompt per tool call, one approval can cover many deletes.
    - The result gives ids, not titles. Telling the user what was removed, or adding an article back, depends on the model still having the titles from `list_entries`.
    - `delete_list_entries` does a little more than call one function, so the tools are no longer all thin wrappers.
- What would make us revisit this decision:
    - The API starts enforcing `list_id`. Test this before removing the pre-check, since that check is the only protection today.
    - Wikimedia adds a batch delete endpoint.
    - Real use shows batches stopping for errors that only affected one entry.
    - The extra GET becomes noticeably slow, or one-DELETE-per-entry hits rate limits.
    - Clients stop prompting for destructive tools, or models delete without asking.
    - The confirmation approach chosen for #8 should also apply here.

## References

- [Issue #7: remove an article from a reading list](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/7)
- [Issue #8: delete a reading list](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/8)
- [Issue #6: size of new reading lists](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/6)
- [rest_v1 spec](https://en.wikipedia.org/api/rest_v1/?spec)
- [MCP spec: tool annotations](https://modelcontextprotocol.io/specification/2025-06-18/schema#toolannotations)
- [ADR 0003](0003-reading-list-setup-as-confirmed-mcp-tool.md)
- [ADR 0005](0005-convert-wikimedia-errors-to-tool-errors.md)
- [ADR 0006](0006-filter-duplicate-names-when-creating-lists.md)
