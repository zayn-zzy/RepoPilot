"""ToolACL tests — role-level gates (read-only, allow/deny sets) layered over
the static permission engine, and definition filtering consistency."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.runtime import ToolACL, build_default_registry  # noqa: E402


class TestACL(unittest.TestCase):
    def _acl(self, **kwargs) -> ToolACL:
        return ToolACL(**kwargs)

    def test_read_only_denies_write_capable_tools(self):
        acl = self._acl(read_only=True)
        for tool in ("write_file", "edit_file", "run_shell", "agent", "skill"):
            r = acl.check(tool, {"file_path": "x", "command": "ls"})
            self.assertEqual(r["action"], "deny", f"{tool} must be denied")
            self.assertIn("read-only", r["message"])

    def test_read_only_allows_read_tools(self):
        acl = self._acl(read_only=True)
        for tool in ("read_file", "list_files", "grep_search", "web_fetch", "tool_search",
                     "enter_plan_mode", "exit_plan_mode"):
            r = acl.check(tool, {"file_path": "x", "pattern": "p", "url": "https://e.com", "query": "q"})
            self.assertEqual(r["action"], "allow", f"{tool} must be allowed, got {r}")

    def test_allowed_tools_gate(self):
        acl = self._acl(allowed_tools={"read_file", "grep_search"})
        self.assertEqual(acl.check("read_file", {"file_path": "x"})["action"], "allow")
        r = acl.check("write_file", {"file_path": "x", "content": "c"})
        self.assertEqual(r["action"], "deny")
        self.assertIn("not in this agent's tool ACL", r["message"])

    def test_denied_tools_explicit(self):
        acl = self._acl(denied_tools={"run_shell"})
        r = acl.check("run_shell", {"command": "echo hi"})
        self.assertEqual(r["action"], "deny")
        self.assertIn("denied by this agent's tool ACL", r["message"])

    def test_plan_mode_still_blocks_shell(self):
        """The static engine (plan mode) keeps the final say under the ACL."""
        acl = self._acl(permission_mode="plan")
        r = acl.check("run_shell", {"command": "ls"})
        self.assertEqual(r["action"], "deny")
        self.assertIn("plan mode", r["message"])

    def test_accept_edits_allows_edit_for_non_read_only(self):
        acl = self._acl(permission_mode="acceptEdits")
        r = acl.check("edit_file", {"file_path": "x", "old_string": "a", "new_string": "b"})
        self.assertEqual(r["action"], "allow")

    def test_filter_definitions_matches_check_gates(self):
        reg = build_default_registry()
        acl = self._acl(read_only=True)
        filtered = acl.filter_definitions(reg.definitions())
        names = {d["name"] for d in filtered}
        for name in ("write_file", "edit_file", "run_shell", "agent", "skill"):
            self.assertNotIn(name, names, f"{name} must be filtered from definitions")
        for name in ("read_file", "list_files", "grep_search", "tool_search"):
            self.assertIn(name, names, f"{name} must survive filtering")

    def test_filter_definitions_allowed_set(self):
        reg = build_default_registry()
        acl = self._acl(allowed_tools={"read_file"})
        names = {d["name"] for d in acl.filter_definitions(reg.definitions())}
        self.assertEqual(names, {"read_file"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
