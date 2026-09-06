"""Data models."""
from .core import Processor


class BaseModel:
    """Base class."""

    def to_dict(self):
        return {}


class User(BaseModel):
    """A user model."""

    def __init__(self, name):
        self.name = name

    def greet(self):
        return f"hello {self.name}"

    class Address:
        """Nested address class."""

        def label(self):
            return "addr"
