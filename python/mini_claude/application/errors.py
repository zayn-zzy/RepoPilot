"""Application-level errors (§9: core exceptions become application
exceptions). ``code`` maps 1:1 to the API error envelope (WP2)."""

from __future__ import annotations


class ApplicationError(RuntimeError):
    """A use-case failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def __str__(self) -> str:
        return self.message
