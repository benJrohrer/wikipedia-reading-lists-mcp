# 0008: Update lists with optional fields, and look up the name to clear a description

- **Status:** Accepted
- **Date:** 2026-11-05

## Context

Issue #9 adds a tool to rename a reading list or change its description, using `PUT /data/lists/{id}`. ADR 0006 chose a separate tool for this over an `overwrite` option on `create_reading_lists`. The issue left three questions open: whether either field should be optional, whether the tool should refuse a name already in use, and whether `destructive_hint` is right.

The endpoint behaves differently from both the spec and the batch create endpoint. All of the following was tested on 2026-11-05 with bearer auth:

- The spec's `list_write` schema requires `name`, but the API accepts a PUT with only `description`, or only `name`. The field left out is kept, not cleared. This differs from batch create, which clears a description that isn't sent (ADR 0006).
- A name used by another of the user's lists is rejected with `readinglists-db-error-duplicate-list`. Descriptions don't have to be unique.
- The default list can't be updated: `readinglists-db-error-cannot-update-default-list`.
- Sending a list's current name succeeds, and repeating that call succeeds too.

An empty string depends on what is sent with it:

| `name` | `description` | API result |
|---|---|---|
| omitted | `""` | `apierror-missingparam-at-least-one-of` |
| `""` | `""` | `apierror-missingparam-at-least-one-of` |
| `""` | `"y"` | `readinglists-db-error-empty-name` |
| `"x"` | `""` | Succeeds, and clears the description |

So the API treats an empty description as missing unless a name comes with it. It can clear a description, but only if the caller also sends the name, even when the name isn't changing. That is easy to get wrong, and getting it wrong in one direction changes the list's name.

The spec has no GET for a single list. The only way to read one list's name is to fetch all of the user's lists.

## Options considered

### Clearing a description without changing the name

#### Option A: The server looks up the name when clearing

When `description` is `""` and `name` is omitted, `update_list` fetches the current name and sends it in the PUT.

- Pros:
    - `update_list(list_id, description="")` works, so the API's quirk stays out of the tool's interface
    - The name is read just before the PUT, so it is as fresh as this API allows
    - Only this one case pays for the lookup
- Cons:
    - An extra paginated GET when clearing
    - A gap between the GET and the PUT, so a rename in that gap is undone
    - A new path that has to handle the list not being found

#### Option B: The caller sends the name

Pass arguments through, document the rule in the docstring, and add a hint for `apierror-missingparam-at-least-one-of`.

- Pros:
    - No extra request or new code path
- Cons:
    - The model has to know the rule and the current name
    - If the model's copy of the name is stale, a call meant to clear a description also renames the list, and the call reports success

A variant was also rejected: keep the server lookup, but use the docstring to suggest passing the name when the model already has it. That saves one GET on an uncommon operation, and brings back the same silent rename.

#### Option C: A `clear_description` flag or a separate tool

- Pros:
    - The intent is explicit
- Cons:
    - Still needs the same name lookup
    - A flag allows contradictory calls, such as `description="y", clear_description=True`

#### Option D: Don't support clearing

Reject an empty description locally and document the limit.

- Pros:
    - The simplest option, with no lookup and no race
- Cons:
    - Users can replace a description but not remove it

#### Option E: Look up the name on every description-only update

This was the first implementation, reached by an `else` that ran whenever `name` was omitted.

- Pros:
    - One code path, and every PUT includes `name`, which matches the spec's schema
- Cons:
    - An extra paginated GET on an ordinary description update, which the API doesn't need
    - The rename race applies to a common path instead of an uncommon one

### Names already in use

#### Option F: Check names first, as in ADR 0006

- Pros:
    - Consistent with `create_lists`
    - Could return the conflicting list to the model
- Cons:
    - An extra paginated GET on every rename
    - Guards against a failure the API already handles safely

#### Option G: Let the API reject the name

- Pros:
    - No extra request
    - No gap between a check and the write, because the API does the check
- Cons:
    - The model only learns about the conflict after the call fails
    - Depends on the API continuing to reject duplicates, which the spec states only as "the new name must not be in use"

## Decision

> **Option A** (look up the name only when clearing) and **Option G** (let the API reject duplicate names), with `name` and `description` both optional.

Each argument has a defined meaning. `description` has three: omitted keeps it, `""` clears it, and text replaces it. `name` has two: omitted keeps it, and text replaces it. An empty name is never valid, so the tool rejects it locally rather than passing on the API's two different errors.

| # | `name` | `description` | What `update_list` does | Requests |
|---|---|---|---|---|
| 1 | omitted | omitted | Raises `name-or-description-required` | None |
| 2 | omitted | `""` | Looks up the name, then sends it with `""` | GET, PUT |
| 3 | omitted | `"y"` | Sends the description | PUT |
| 4 | `""` | omitted | Raises `non-empty-name-required` | None |
| 5 | `""` | `""` | Raises `non-empty-name-required` | None |
| 6 | `""` | `"y"` | Raises `non-empty-name-required` | None |
| 7 | `"x"` | omitted | Sends the name | PUT |
| 8 | `"x"` | `""` | Sends both | PUT |
| 9 | `"x"` | `"y"` | Sends both | PUT |

A won over B because requiring the name for an unrelated change invites mistakes, and the mistake is a silent rename. C needs the same lookup and adds interface. D leaves a gap that a small lookup can fill. E works, but it charges every description update for a workaround that one case needs.

G won over F because the API already fails safely here. ADR 0006 needed a filter because batch create silently overwrites data. This endpoint returns an error and changes nothing, so a pre-check would add a request without protecting anything. The docstring still tells the model to check for existing names first, and the `duplicate-list` hint tells it how to recover.

### Row 2 errors

`_fetch_list_name` raises `readinglists-db-error-no-such-list` when the id isn't in the user's lists, the same code the API uses, so the existing hint applies. A default list passes the lookup, and the PUT then returns `cannot-update-default-list`, so that needs no extra code.

### Local validation errors

The two local checks raise `WikimediaAPIError` with their own codes, so they follow the existing path through `ERROR_HINTS` (ADR 0005). The hint for an empty name tells the model to omit `name` to keep it, because a model may send `""` meaning "no change."

### Annotations and confirmation

`destructive_hint=True`, because the old name or description is overwritten and this server has no way to restore it. MCP defines `destructiveHint: false` as "the tool performs only additive updates," which this is not. `idempotent_hint=True`, because repeating the same call was tested and leaves the same result. The tool also tells the model to get the user's explicit permission first, the same model-reliant approach as ADR 0003.

### One tool

There is one `update_list` tool because the API has one PUT endpoint for both fields. Separate tools for renaming and describing were not compared.

## Consequences

- What this makes easier:
    - Clearing a description is one simple call, with no name needed. Tested 2026-11-05 through the MCP Inspector's CLI mode: the description was cleared.
    - Most updates cost a single PUT. Tested 2026-11-05: a description-only update through the MCP Inspector behaved as expected, and a rename sent with a description updated both fields.
    - The API's empty-string rules stay inside `update_list`, and `at-least-one-of` should no longer reach the tool.
    - Duplicate names and the default list are handled by the API, with hints for recovery.
- What this makes harder, or the costs we accept:
    - Row 2 fetches all of the user's lists to read one name.
    - **Race window:** on row 2, a rename between the GET and the PUT is silently undone. We accept this because it needs another client to rename the same list within moments, on an uncommon path.
    - `""` clears a description with no error. A model that fills optional arguments with `""` meaning "no change" would wipe it, and only the docstring guards against that.
    - The same mistake can give different errors. Tested 2026-11-05: a direct update to a deleted list returned `readinglists-db-error-list-deleted`, while clearing the description on that list returned `readinglists-db-error-no-such-list`, because the lookup doesn't find it. Both tell the model the list can't be used.
    - Row 3 depends on undocumented behavior, because the spec's schema requires `name`. If the API starts enforcing that, row 3 would likely fail with an error rather than lose data.
- What would make us revisit this decision:
    - The API accepts an empty description on its own. The lookup could then be removed.
    - Wikimedia adds a GET for a single list. The lookup could use it.
    - Models clear descriptions they meant to leave alone.
    - The API stops rejecting duplicate names on update. Option F would then be needed, for the same reason as ADR 0006.
    - The API starts requiring `name` on every PUT.

## References

- [Issue #9: rename a reading list or change its description](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/9)
- [rest_v1 spec](https://en.wikipedia.org/api/rest_v1/?spec)
- [ReadingLists extension source (GitHub mirror)](https://github.com/wikimedia/mediawiki-extensions-ReadingLists/blob/master/src/ReadingListRepository.php)
- [MCP spec: tool annotations](https://modelcontextprotocol.io/specification/2025-06-18/schema#toolannotations)
- [ADR 0003](0003-reading-list-setup-as-confirmed-mcp-tool.md)
- [ADR 0005](0005-convert-wikimedia-errors-to-tool-errors.md)
- [ADR 0006](0006-filter-duplicate-names-when-creating-lists.md)
