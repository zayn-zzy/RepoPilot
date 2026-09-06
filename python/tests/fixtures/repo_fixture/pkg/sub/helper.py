"""Helpers for the sub package."""
from ..utils import add as add_alias
from .. import core


def nested_helper(x):
    return add_alias(x, 1)


class Helper:
    def assist(self):
        return core.process([1, 2])
