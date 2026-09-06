"""Core processing module."""
import os
from typing import List, Optional
from .utils import add
from .sub.helper import nested_helper, Helper
from pkg.models import BaseModel


def process(items: List[int]) -> int:
    return add(*items)


class Processor:
    """A processor with methods."""

    def __init__(self, factor: int = 1):
        self.factor = factor

    def scale(self, value: int) -> int:
        return value * self.factor

    async def run_async(self):
        return 1

    class Inner:
        """Nested class."""

        def inner_method(self):
            return "inner"
