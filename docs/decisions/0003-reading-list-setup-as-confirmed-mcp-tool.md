# 0003: Reading list setup as a confirmed MCP tool

- **Status:** Accepted
- **Date:** 2026-10-18

## Context

The API spec says that POST /data/lists/setup "Must precede other list operations." Users may begin using this tool before or after opting into the reading lists feature. The spec lists no status endpoint; the status is learned from the not-set-up error. Setup/opting-in is a write to the user account, and is reversed, with data deletion, by POST /data/lists/teardown. Calling setup on an account that's already set up returns 400 readinglists-db-error-already-set-up, and existing lists are unchanged. Verified 2026-10-18 with both cookie auth (mediawiki.org sandbox) and bearer auth (our client). The solution should ensure that setup happens once, only with consent, and an extra or mistaken call has no effect on existing data. The solution should also be simple and limit user friction.

## Options considered

### Option A: One-time manual setup outside the server

- Pros:
    - Explicit consent, as the server is not acting on behalf of the user
- Cons:
    - An extra step for new users
    - Unable to be fixed by LLM, just reported on

### Option B: Automatic setup when the server sees a not-set-up error

- Pros:
    - No friction for the user
- Cons:
    - Opts the user in to reading lists without asking
    - A hidden write that happens alongside a read

### Option C: MCP tool that LLM calls after asking the user

- Pros:
    - Enables consent between the user and the server
    - Provides visibility into the opting in process
    - The LLM can explain what setup does
- Cons:
    - The server can't enforce "ask first", and the LLM could just run the /setup tool on its own (see Consequences).

## Decision

> **Option C**: MCP tool that LLM calls after asking the user

Creating an MCP tool that the LLM calls provides visibility in to the process, handles the not-set-up error case when it appears (with explicit consent by the user), and all with minimal friction.

### Exposing teardown

**Not Exposed**. Exposing the teardown endpoint presents too much risk, as a mistaken call would delete all of a user's reading lists and entries. It also has no use case in this project.

## Consequences

- What this makes easier:
    - Provides an LLM guided setup for the first run
    - already-set-up errors can be treated as a success
- What this makes harder, or the costs we accept:
    - This solution does rely heavily on a cooperative LLM, that follows the tool docstring and error message (both indicating to ask for user consent before calling the setup tool). The server will have no way of ensuring that the user actually consented to opting-in. We accept this consequence given that the impact is low - it only enables the feature, and probably creates a default list - but it can't be undone without teardown, which deletes data (for a freshly set-up account, probably just the created default list). A repeated or unnecessary call has no effect, so the worst case is a single opt-in.
    - Update 2026-10-20:
        - Marked setup_reading_lists tool as non-destructive in annotation hint. It may reduce the client's caution around calling the function, but the consent instruction in the  docstring and the not-set-up hint are still in place. Consent relies on them instead of the annotation. The reason for this is accuracy - opt-in only writes and deletes nothing, it is also idempotent.
- What would make us revisit this decision:
    - If Wikimedia changes the opt-in model
    - If the MCP Client stops prompting for tool approval
    - If a real need for teardown arises, which would require a stronger safeguard than a single tool call

## References
- [rest_v1 spec](https://en.wikipedia.org/api/rest_v1/?spec)
- [Not-set-up error](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/2#issuecomment-6254884632)
- [ADR 0002](0002-use-rest-api-for-reading-lists.md)
- [Already-set-up error](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/2#issuecomment-6255577523)