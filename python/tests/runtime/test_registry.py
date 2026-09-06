"""ToolRegistry tests — registration, definitions, dispatch, deferred
activation, per-instance isolation, and ACL enforcement at dispatch time."""

import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.runtime import Tool, ToolACL, build_default_registry  # noqa: E402
from mini_claude.runtime.registry import ToolRegistry  # noqa: E402
from mini_claude.tools import tool_definitions  # noqa: E402


def _echo_tool(name: str = "echo") -> Tool:
    return Tool(name, "echo input", {"type": "object", "properties": {}},
                lambda inp: f"{name}:{inp.get('text', '')}")


class TestRegistry(unittest.IsolatedAsyncioTestCase):
    def test_build_default_registry_seeds_all_builtins(self):
        reg = build_default_registry()
        self.assertEqual(set(reg.names()), {t["name"] for t in tool_definitions})

    def test_register_duplicate_raises(self):
        reg = ToolRegistry()
        reg.register(_echo_tool())
        with self.assertRaises(ValueError):
            reg.register(_echo_tool())

    def test_unregister(self):
        reg = ToolRegistry()
        reg.register(_echo_tool())
        self.assertTrue(reg.unregister("echo"))
        self.assertFalse(reg.unregister("echo"))
        self.assertIsNone(reg.get("echo"))

    def test_definitions_format(self):
        reg = build_default_registry()
        defs = {d["name"]: d for d in reg.definitions()}
        rf = defs["read_file"]
        self.assertEqual(rf["input_schema"]["required"], ["file_path"])
        # deferred flag preserved in definitions(), stripped in active_definitions()
        pm = defs["enter_plan_mode"]
        self.assertTrue(pm["deferred"])
        active = {d["name"]: d for d in reg.active_definitions()}
        self.assertNotIn("enter_plan_mode", active)
        self.assertNotIn("deferred", active["read_file"])

    async def test_dispatch_unknown_tool(self):
        reg = build_default_registry()
        result = await reg.dispatch("no_such_tool", {})
        self.assertEqual(result, "Unknown tool: no_such_tool")

    async def test_dispatch_builtin_read_file(self):
        reg = build_default_registry()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "hello.txt"
            p.write_text("line1\nline2")
            result = await reg.dispatch("read_file", {"file_path": str(p)})
            self.assertIn("line1", result)
            self.assertIn("line2", result)

    async def test_dispatch_custom_tool_sync_handler(self):
        reg = ToolRegistry()
        reg.register(_echo_tool())
        result = await reg.dispatch("echo", {"text": "hi"})
        self.assertEqual(result, "echo:hi")

    async def test_dispatch_custom_tool_async_handler(self):
        async def _h(inp):
            return f"async:{inp.get('text', '')}"

        reg = ToolRegistry()
        reg.register(Tool("aecho", "async echo", {"type": "object", "properties": {}}, _h))
        result = await reg.dispatch("aecho", {"text": "yo"})
        self.assertEqual(result, "async:yo")

    async def test_tool_search_activates_deferred_per_instance(self):
        reg_a = build_default_registry()
        reg_b = build_default_registry()
        self.assertNotIn("enter_plan_mode", {d["name"] for d in reg_a.active_definitions()})
        self.assertNotIn("enter_plan_mode", {d["name"] for d in reg_b.active_definitions()})

        result = await reg_a.dispatch("tool_search", {"query": "plan mode"})
        self.assertIn("enter_plan_mode", result)
        self.assertIn("enter_plan_mode", {d["name"] for d in reg_a.active_definitions()})
        # reg_b untouched — no cross-instance leakage
        self.assertNotIn("enter_plan_mode", {d["name"] for d in reg_b.active_definitions()})

    async def test_tool_search_no_match(self):
        reg = build_default_registry()
        result = await reg.dispatch("tool_search", {"query": "zzz-nothing"})
        self.assertEqual(result, "No matching deferred tools found.")

    async def test_dispatch_enforces_read_only_acl(self):
        reg = build_default_registry()
        acl = ToolACL(read_only=True)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new.txt"
            result = await reg.dispatch("write_file", {"file_path": str(target), "content": "x"},
                                        acl=acl)
            self.assertIn("Action denied", result)
            self.assertFalse(target.exists())

    async def test_dispatch_enforces_allowed_tools_acl(self):
        reg = build_default_registry()
        acl = ToolACL(allowed_tools={"read_file"})
        result = await reg.dispatch("grep_search", {"pattern": "x"}, acl=acl)
        self.assertIn("not in this agent's tool ACL", result)

    async def test_read_before_edit_flows_through_registry(self):
        """Built-in dispatch keeps the original read-before-edit protection."""
        reg = build_default_registry()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "existing.txt"
            p.write_text("old")
            # No read first → the original executor must refuse the write.
            result = await reg.dispatch("write_file", {"file_path": str(p), "content": "new"},
                                        read_file_state={})
            self.assertIn("must read this file before", result)
            self.assertEqual(p.read_text(), "old")


if __name__ == "__main__":
    unittest.main(verbosity=2)
