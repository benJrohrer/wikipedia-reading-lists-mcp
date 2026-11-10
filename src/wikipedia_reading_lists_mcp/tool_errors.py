"""Turn Wikimedia API errors into messages the model can act on (ADR 0005).

ERROR_HINTS maps error codes to next steps for the model. The hints name tools,
so they live in the MCP layer, not with the error classes.
"""

import functools

from mcp.server.mcpserver.exceptions import ToolError

from wikipedia_reading_lists_mcp.errors import WikimediaAPIError

ERROR_HINTS: dict[str, str] = {
    "readinglists-db-error-cannot-delete-default-list": (
        "The default list cannot be deleted. Do not retry this call and let the user "
        "know about this limitation. If that list was passed by mistake, use "
        "list_reading_lists to get all of the user's lists with their ids."
    ),
    "readinglists-db-error-cannot-update-default-list": (
        "The default list cannot be updated. Do not retry this call and let the user "
        "know about this limitation. If that list was passed by mistake, use "
        "list_reading_lists to get all of the user's lists with their ids."
    ),
    "readinglists-db-error-duplicate-list": (
        "The list that would be created or updated has the same name as an existing "
        "list of this user, which the API forbids. Choose a different name and try "
        "again. Use list_reading_lists first to ensure that the chosen name does not "
        "conflict."
    ),
    "readinglists-db-error-list-deleted": (
        "The given list has been deleted. Use list_reading_lists to get a list of the "
        "user's current lists. Don't retry this call and notify the user."
    ),
    "readinglists-db-error-not-set-up": (
        "The user has not opted in to the Reading List feature. "
        "To enable the feature, ask the user if you can opt in to this feature on "
        "their behalf. Then, and only with their explicit approval, can you use the "
        "setup_reading_lists tool to enable this feature."
    ),
    "readinglists-db-error-no-such-list": (
        "No list exists with the given id. Call "
        "list_reading_lists to get all lists with their ids. Then you can use an id "
        "from that list to reference it where a list id is required."
    ),
    "readinglists-db-error-no-such-list-entry": (
        "No list entry exists with the given id. Call list_entries with the given "
        "list_id. Then you can use an id from that list to reference it where an entry "
        "id is required. Call list_reading_lists to get all lists, if you need a "
        "list_id as well."
    ),
    "readinglists-db-error-not-own-list": (
        "The user does not own the list. Call list_reading_lists to get the "
        "lists which belong to the user, and use those list ids to perform other "
        "operations which require a list_id."
    ),
    "readinglists-db-error-not-own-list-entry": (
        "The user does not own the list entry. Call list_reading_lists to get the "
        "lists which belong to the user, then list_entries with one of those list ids "
        "to get the entries on that list."
    ),
    "readinglists-db-error-no-such-project": (
        "The given project name is not a recognized Wikimedia project. A project name "
        "is the base url such as 'https://en.wikipedia.org' for the English language "
        "wikipedia project. This error interrupts the create_list_entries tool. Any "
        "entries before the entry with an incorrect project name were created, and any "
        "entries following the entry that caused this error will not have been "
        "created. Use the correct project name for any entries you were trying to "
        "create that used the incorrect project name and submit the whole batch again. "
        "After this subsequent call, and in this case only, ignore the duplicate flag "
        "on the responses which came before the first entry with the incorrect "
        "project, and do not report these to the user as it may just indicate that "
        "these response were added in the preceding call before the incorrect project "
        "name. Notify the user of the updated project name and for which entries it "
        "was used for."
    ),
    "name-or-description-required": (
        "When updating a list, at least one of name or description is required. If you "
        "are trying to update the name or description, include one or both of them as "
        "arguments to this tool."
    ),
    "network": (
        "A network error occurred. Tell the user to check the internet connection."
    ),
    "non-empty-name-required": (
        "When updating a list, the name field, if included, must be non-empty. Omit "
        "the name to keep the current name."
    ),
    "retry-after": (
        "The connection is rate-limited. The server already retried, or decided the "
        "wait was too long to handle on its own. The request that produced the rate "
        "limit error wasn't carried out, so there is no need to check whether it went "
        "through. This error message may contain a retry-after note as a time to "
        "wait in seconds. If it does, and you have the ability to wait (e.g. by "
        "calling `sleep`), you can wait that long and try again. If it does not, or "
        "you don't have the ability to wait, tell the user to try again after the "
        "given wait time if there is one, or after a few minutes if there isn't. Rate "
        "limits are per account, so if the user is using the API elsewhere (such as "
        "through the mobile app which generates API calls for downloading saved "
        "articles), that usage may be impacting the rate limits."
    ),
    "timeout": (
        "The connection timed out. Retry once; if it fails again, tell the user to "
        "check the internet connection."
    ),
    "401": (
        "The user's token is bad. The user must place a proper token in the "
        "WIKIMEDIA_ACCESS_TOKEN environment variable or .env file. The server must be "
        "restarted for this to take effect."
    ),
    "403": (
        "The user's token does not grant them permission to do the requested action. "
        "Tell the user to check if their token has the 'Access private information' "
        "grant."
    ),
}


def error_hint(code: str | None) -> str | None:
    """Return the hint for an error code.

    Looks up code in ERROR_HINTS dict to see if a hint is defined.
    For 500 errors, returns generic internal error message. Otherwise returns None.
    """
    if not code:
        return None

    if code in ERROR_HINTS:
        return ERROR_HINTS[code]
    elif code.isdecimal() and code.startswith("5"):
        return "Wikipedia returned an internal error. Try again later."
    else:
        return None


def handle_wikimedia_errors(fn):
    """Re-raise WikimediaAPIError from a tool as ToolError.

    Re-raising the error as a ToolError ensures the model sees why it failed.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwds):
        try:
            return await fn(*args, **kwds)
        except WikimediaAPIError as e:
            hint = error_hint(e.code)
            message = (str(e) + f" HINT: {hint}") if hint else str(e)
            raise ToolError(message) from e

    return wrapper
