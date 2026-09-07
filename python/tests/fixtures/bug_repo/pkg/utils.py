"""Utility functions for the sample bug repository."""


def add(a, b):
    return a + b


def multiply(a, b):
    # INJECTED BUG: multiplies by adding. The unit test below must fail.
    return a + b
