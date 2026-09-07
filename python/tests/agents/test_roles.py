"""Role definitions and ACL tests — per-role tool sets match the spec,
read-only roles are blocked from writing, coder can write."""

import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.agents import (  # noqa: E402
    READ_ONLY_ROLES,
    ROLE_NAMES,
    ROLE_PROMPTS,
    ROLE_TOOL_SETS,
    build_role_registry,
    role_acl,
)
from mini_claude.agents.artifact import ArtifactMailbox  # noqa: E402
from mini_claude.repo import RepositoryIndex  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "repo_fixture"
WRITE_TOOLS = {"write_file", "edit_file", "run_shell"}


class TestRoleDefinitions(unittest.TestCase):
    def test_exactly_five_roles(self):
        self.assertEqual(set(ROLE_NAMES), {"planner", "explorer", "coder", "tester", "reviewer"})

    def test_every_role_has_distinct_prompt(self):
        prompts = set(ROLE_PROMPTS.values())
        self.assertEqual(len(prompts), 5)
        for role, prompt in ROLE_PROMPTS.items():
            self.assertIn(role, prompt.lower())

    def test_read_only_roles_have_no_write_tools(self):
        for role in ("planner", "explorer", "reviewer"):
            self.assertEqual(ROLE_TOOL_SETS[role] & WRITE_TOOLS, set(), f"{role} must not write")

    def test_coder_can_write(self):
        self.assertTrue(WRITE_TOOLS.issubset(ROLE_TOOL_SETS["coder"]))

    def test_tester_has_verification_tools(self):
        self.assertTrue({"run_tests", "run_lint", "parse_failure"}.issubset(ROLE_TOOL_SETS["tester"]))

    def test_explorer_has_search_tools(self):
        self.assertTrue({"symbol_search", "dependency_search", "semantic_search", "git_log"}
                        .issubset(ROLE_TOOL_SETS["explorer"]))

    def test_reviewer_has_diff_and_deps(self):
        self.assertTrue({"git_diff", "dependency_search"}.issubset(ROLE_TOOL_SETS["reviewer"]))

    def test_every_role_can_publish(self):
        for role in ROLE_NAMES:
            self.assertIn("publish_artifact", ROLE_TOOL_SETS[role])


class TestRoleACL(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.idx = RepositoryIndex(FIXTURE)
        self.idx.build()
        self.registry = build_role_registry(self.idx, ArtifactMailbox())

    async def test_acl_check_denies_writes_for_read_only_roles(self):
        for role in ("planner", "explorer", "reviewer"):
            acl = role_acl(role)
            r = acl.check("write_file", {"file_path": "x", "content": "y"})
            self.assertEqual(r["action"], "deny", f"{role} must be denied write_file")

    async def test_acl_check_allows_writes_for_coder(self):
        # The role gate passes for coder; with acceptEdits the static engine
        # also lets new-file writes through without confirmation.
        acl = role_acl("coder", permission_mode="acceptEdits")
        r = acl.check("write_file", {"file_path": "x", "content": "y"})
        self.assertEqual(r["action"], "allow")

    async def test_dispatch_rejects_explorer_write(self):
        """ACL enforcement at dispatch: explorer's runtime calls write_file
        → denied, file untouched."""
        acl = role_acl("explorer")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new.txt"
            result = await self.registry.dispatch(
                "write_file", {"file_path": str(target), "content": "owned"}, acl=acl)
            self.assertIn("Action denied", result)
            self.assertFalse(target.exists())

    async def test_dispatch_allows_coder_write(self):
        # acceptEdits lets new-file writes past the static engine — the coder
        # role gate itself is the only layer left, and it allows.
        acl = role_acl("coder", permission_mode="acceptEdits")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new.txt"
            # read-before-edit: the path is new, so the original executor's
            # guard doesn't apply (only existing files need a prior read).
            result = await self.registry.dispatch(
                "write_file", {"file_path": str(target), "content": "made by coder"}, acl=acl)
            self.assertIn("Successfully wrote", result)
            self.assertTrue(target.exists())

    async def test_dispatch_rejects_reviewer_write(self):
        acl = role_acl("reviewer")
        result = await self.registry.dispatch(
            "edit_file", {"file_path": "x", "old_string": "a", "new_string": "b"}, acl=acl)
        self.assertIn("Action denied", result)

    def test_read_only_roles_see_no_write_tools_in_definitions(self):
        for role in ("planner", "explorer", "reviewer"):
            defs = role_acl(role).filter_definitions(self.registry.active_definitions())
            names = {d["name"] for d in defs}
            self.assertEqual(names & WRITE_TOOLS, set(), f"{role} must not see write tools")

    def test_coder_sees_write_tools(self):
        names = {d["name"] for d in
                 role_acl("coder").filter_definitions(self.registry.active_definitions())}
        self.assertTrue(WRITE_TOOLS.issubset(names))


if __name__ == "__main__":
    unittest.main(verbosity=2)
