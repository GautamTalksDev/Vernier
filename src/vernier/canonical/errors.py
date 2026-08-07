"""Errors raised by canonicalization."""


class CanonicalError(ValueError):
    """Base error for canonicalization failures."""


class ValueTooLargeError(CanonicalError):
    """Raised when a single value exceeds the size limit."""

    def __init__(self, size: int, limit: int) -> None:
        self.size = size
        self.limit = limit
        super().__init__(
            f"single value is {size} bytes; limit is {limit} bytes (8 MiB)"
        )


class JcsError(CanonicalError):
    """Raised when a value cannot be represented under RFC 8785 / I-JSON."""
