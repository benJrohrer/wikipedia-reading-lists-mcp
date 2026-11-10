"""Shapes of the data the tools accept and return.

The MCP SDK builds each tool's input and output schemas from these TypedDicts.
"""

from typing import NotRequired, TypedDict


class CreateEntryResult(TypedDict):
    """A dictionary with fields for an entry created or duplicate response."""

    id: int
    listId: int
    project: str
    title: str
    created: str
    updated: str
    duplicate: bool


class CreateListsResult(TypedDict):
    """A dictionary with the lists of created, updated, and excluded reading lists."""

    created: list[dict]
    updated: list[dict]
    excluded: list[dict]


class FetchedEntryResult(TypedDict):
    """A dictionary with id, project, title, created, and updated fields for entry."""

    id: int
    project: str
    title: str
    created: str
    updated: str


class FailedDelete(TypedDict):
    """The entry whose deletion failed, with the error that stopped the batch."""

    entry_id: int
    code: str | None
    message: str
    hint: NotRequired[str]


class DeleteEntriesResult(TypedDict):
    """A dictionary with the outcome of each entry in a delete attempt."""

    deleted: list[int]
    failed: FailedDelete | None
    excluded: list[int]
    not_attempted: list[int]


class EntrySpec(TypedDict):
    """An entry to create on a reading list."""

    project: str
    title: str


class ListSpec(TypedDict):
    """A reading list to create."""

    name: str
    description: NotRequired[str]


class ListResult(TypedDict):
    """A typical reading list result from listing or updating a reading list."""

    id: int
    name: str
    default: NotRequired[bool]
    description: NotRequired[str]
    created: str
    updated: str
