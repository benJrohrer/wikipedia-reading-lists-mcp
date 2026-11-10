"""Exceptions raised by the Wikimedia API layer (ADR 0005)."""


class WikimediaAPIError(Exception):
    """Raised when Wikimedia API reports an error or returns an unexpected response."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class WikimediaAnonError(WikimediaAPIError):
    """Raised when the Wikimedia API returns an anonymous user in userinfo request."""

    def __init__(self) -> None:
        super().__init__(
            message="The Wikimedia API returned an anonymous user", code="anon"
        )


class WikimediaRateLimitError(WikimediaAPIError):
    """Raised when the Wikimedia API returns a 429 error."""

    def __init__(self, retry_after: float | None) -> None:
        suffix = (
            f" Retry after: {retry_after} seconds" if retry_after is not None else ""
        )
        super().__init__(
            message=(
                "The Wikimedia API returned a 429 error (Too Many Requests)." + suffix
            ),
            code="retry-after",
        )
        self.wait = retry_after
