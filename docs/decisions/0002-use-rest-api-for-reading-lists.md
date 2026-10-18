# 0002: Use the REST API for reading lists

- **Status:** Proposed
- **Date:** 2026-10-17

## Context

Wikipedia reading lists are exposed through two different APIs: the older Action API (api.php through the readinglists module) and the newer REST API. The Action API's [readinglist module](https://en.wikipedia.org/w/api.php?action=help&modules=query+readinglists) is marked internal/unstable and indicates that it should not be used, as of 2026-10-17. The reading list REST API is still marked unstable. This project has already used the Action API for meta=userinfo, for which there is no equivalent REST endpoint, although this is not strictly necessary for reading list calls. For this project, we should follow the maintainers' supported interfaces as closely as possible, opt for using published specs, and keep error handling as simple as possible. ADR 0001 is unaffected.

## Options considered

### Option A: Action API throughout

- Pros:
    - Consistency
    - Only one response/error shape to parse
    - Useful tools such as action=help calls and a sandbox
- Cons:
    - Officially marked as internal, with a warning not to use

### Option B: REST API throughout

- Pros:
    - One API style and one error shape across all API calls
- Cons:
    - Lacks the ability to get the signed in user's name, which are useful for diagnostic errors and logs

### Option C: REST API for reading lists, Action API for userinfo

- Pros:
    - Keep the userinfo functionality intact
    - Uses REST API for Reading List functionality, which is the more officially supported method
    - Published rest_v1 spec with real HTTP status codes
- Cons:
    - slightly less consistency, requires handling two different error/response formats
    - Posting to /data/lists/ is deprecated and requires posting to /batch instead

## Decision

> **Option C**: REST API for reading lists, Action API for userinfo.

The Action API is explicitly marked as internal/unstable, and using the REST API should make this project more future-facing. Likewise, meta=userinfo is a core MediaWiki module with no internal banner, and keeping this functionality will be a useful diagnostic and convenience tool, while only introducing slightly more complexity to the code. The REST API is unstable, but using it provides some contract with the developers. In regards to unstable API versions, Wikimedia says it "will reach out to users and make a concerted effort to avoid breaking existing consumers." [API_versioning](https://www.mediawiki.org/wiki/API_versioning). Both the REST API and the Action API are subject to change, but the REST maintainers state an intent to contact users and avoid breaking them, and the Action API module explicitly offers no such commitment.

### Which REST API URL to use?

The reading lists module can be reached via the REST API through two different URLs - */api/rest_v1/data/lists/* (public) and */w/rest.php/readinglists/v0/lists/* (implementation URL). Despite the sunsetting of RESTBase, the developers have committed to keeping the public URL intact. Furthermore, the public URL has a published spec, whereas the implementation URL one does not.

Therefore, for now, we will use the public URL and monitor for any changes to its structure.

## Consequences

- What this makes easier:
    - This decision attempts to avoid potential unannounced changes that may come from relying on the Action API's reading list module.
- What this makes harder, or the costs we accept:
    - This will add another case of error & response handling, in addition to the Action API calls for userinfo that have already been added. (See [Issue #2](https://github.com/benJrohrer/wikipedia-reading-lists-mcp/issues/2))
    - The Reading Lists endpoint of the REST API is still unstable, and subject to change.
    - We must confirm whether or not a bearer token works for REST requests, the spec for which says a MediaWiki session cookie is required.
        - **RESOLVED**: A bearer token works on rest_v1, tested on 2026-10-18.
    - Writes document a csrf_token, we need to check if OAuth makes it unnecessary
        - **RESOLVED**: Using a bearer token negates needing a csrf_token for writes, tested on 2026-10-18 against POST /data/lists/setup
- What would make us revisit this decision:
    - /api/rest_v1/ may be phased out over time, and may require us changing the URL
    - The internal banner is removed from the ReadingLists modules of the Action API

## References
- [T336693](https://phabricator.wikimedia.org/T336693) on the public REST URL
- [Reading Lists Action API](https://en.wikipedia.org/w/api.php?action=help&modules=query+readinglists) - Reading Lists module of Action API is internal/unstable
- [rest_v1 spec](https://en.wikipedia.org/api/rest_v1/?spec)
- [API Versioning](https://www.mediawiki.org/wiki/API_versioning)
- [RESTBase deprecation](https://www.mediawiki.org/wiki/RESTBase/deprecation)