"""Utility functions."""
import json


def add(a, b):
    """Add two numbers."""
    return a + b


async def async_fetch():
    return "ok"


def outer(n):
    def inner(m):
        """Nested helper."""
        return m * n
    return inner
