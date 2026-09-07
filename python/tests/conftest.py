"""Pytest configuration for the RepoPilot test suite.

The fixtures/ directory contains sample repositories (repo_fixture,
bug_repo) — real code with its own tests. It must never be collected as
part of this suite: bug_repo/tests/*.py belongs to the Phase 7
verification pipeline's own runs, and importing it here breaks
collection (its pkg package is not on this suite's sys.path).
"""

collect_ignore = ["fixtures"]
