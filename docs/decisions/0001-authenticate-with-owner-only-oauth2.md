# 0001: Authenticate with an owner-only OAuth 2.0 consumer

- **Status:** Proposed
- **Date:** 2026-10-16

## Context

The calls to Wikipedia's API for interacting with reading lists require authentication. Wikipedia/Wikimedia logins are the same SUL (single user login) account, managed by CentralAuth. A Wikimedia developer account is not needed. The server is local and personal for now, and it runs over stdio with secrets kept in .env. The solution should be minimally scoped to what is necessary for this first version. Authentication should be easily revocable and require minimal authentication logic. The decision should also easily allow for further development to support more users, if desired in the future.

## Options considered

### Option A: OAuth 2 owner-only

Register on meta.wikimedia.org and choose "only for my account". Creates one access token that can be stored in .env and passed through to API calls.

- Pros:
    - Simple and officially preferred method
    - Scoped grants and revocable
    - Uses *Authorization:Bearer* header which will support upgrade path to full OAuth2 flow, if desired
- Cons:
    - Some setup required for a new user (registering and getting an access token)
    - Only works for one account defined in .env
    - The bearer token works for anyone who has access to it

### Option B: OAuth 1.0a owner-only

Register on meta.wikimedia.org for OAuth 1 and choose "only for my account". Creates 4 different strings which would be used to sign requests.

- Pros:
    - Has compatibility with tools such as Pywikibot, which might be considered in the future.
- Cons:
    - More complicated
    - Requires signing logic
    - Older standard

### Option C: BotPassword with action=login

Special:BotPasswords provides a "User@botname"/password pair with permission grants. Session information is stored in cookies.

- Pros:
    - Scoped grants and easily revocable
- Cons:
    - Requires managing session state with a login token, cookie jar, and logging in again when the session expires

### Option D: Full OAuth 2 authorization code flow

User approves the app in a browser and the server receives a token through a callback.

- Pros:
    - Potentially easier setup for new users with browser based interface
- Cons:
    - The MCP server client would need admin approval before being used
    - The server would also need to have a callback/redirect handler to follow the full OAuth 2 flow
    - The server would probably need to be registered as a non-confidential client and use PKCE instead of a client secret.

### Option E: Main password with action=login

Similar to Option C only with the user's actual main password.

- *Rejected because*:
    - Requires managing session state
    - Requires storing password in .env in plain text, possibly leaked
    - No scoping, full account access
    - Does not work with 2FA
    - Is deprecated

## Decision

> **Option A**: OAuth 2 owner-only

Creating and using an OAuth 2 owner-only access token is the simplest option, and has fewer drawbacks than the other options. It is the best fit for this simple one-user approach at the moment. If the project evolves further, we might consider **Option D** again to make the authentication flow easier for users not familiar with using a .env file and creating access tokens.

## Consequences

- What this makes easier:
    - Auth is a single header, with no code required for logging in or refreshing
    - We only need to store one token in the .env file
- What this makes harder, or the costs we accept:
    - Introduces minimal user setup in order to run: getting an access token and storing it in .env
    - Must take care to never log the token
- What would make us revisit this decision:
    - Needing to use an OAuth1-only tool such as Pywikibot
    - Wanting to provide the full OAuth2 flow with in-browser authentication
    - If the token expires quickly, we may need to reconsider another option

## References

- [Reading Lists](https://www.mediawiki.org/wiki/Extension:ReadingLists)
- [API Login](https://www.mediawiki.org/wiki/API:Login)
- [Wikimedia APIs](https://www.mediawiki.org/wiki/Wikimedia_APIs)
- [Owner-only Consumers](https://www.mediawiki.org/wiki/OAuth/Owner-only_consumers)
