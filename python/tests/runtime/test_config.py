"""AgentConfig validation tests — invalid configs must raise at validate()
time, before any AgentRuntime is constructed."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.runtime import AgentConfig, ROLE_PROFILES  # noqa: E402


class TestConfigValidation(unittest.TestCase):
    def test_valid_config_passes(self):
        cfg = AgentConfig(model="test-model", api_key="k")
        self.assertIs(cfg.validate(), cfg)

    def test_empty_model_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            AgentConfig(model="").validate()
        self.assertIn("model", str(ctx.exception))

    def test_non_string_model_rejected(self):
        with self.assertRaises(ValueError):
            AgentConfig(model=123).validate()  # type: ignore[arg-type]

    def test_invalid_permission_mode_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            AgentConfig(permission_mode="wild").validate()
        self.assertIn("permission_mode", str(ctx.exception))

    def test_unknown_role_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            AgentConfig(role="space-cadet").validate()
        self.assertIn("role", str(ctx.exception))

    def test_negative_budget_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            AgentConfig(max_cost_usd=-0.01).validate()
        self.assertIn("max_cost_usd", str(ctx.exception))

    def test_zero_turns_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            AgentConfig(max_turns=0).validate()
        self.assertIn("max_turns", str(ctx.exception))

    def test_read_only_conflicts_with_bypass_permissions(self):
        with self.assertRaises(ValueError) as ctx:
            AgentConfig(read_only=True, permission_mode="bypassPermissions").validate()
        self.assertIn("bypassPermissions", str(ctx.exception))

    def test_unknown_tool_name_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            AgentConfig(tool_names=("read_file", "not_a_tool")).validate()
        self.assertIn("not_a_tool", str(ctx.exception))

    def test_from_role_applies_profile(self):
        planner = AgentConfig.from_role("planner", api_key="k")
        self.assertTrue(planner.read_only)
        self.assertEqual(set(planner.tool_names), {"read_file", "list_files", "grep_search", "tool_search"})

        coder = AgentConfig.from_role("coder", api_key="k")
        self.assertFalse(coder.read_only)
        self.assertIn("write_file", coder.tool_names)

        general = AgentConfig.from_role("general", api_key="k")
        self.assertFalse(general.read_only)
        self.assertIsNone(general.tool_names)

    def test_from_role_unknown_raises(self):
        with self.assertRaises(ValueError):
            AgentConfig.from_role("nope")

    def test_overrides_win_over_profile(self):
        cfg = AgentConfig.from_role("planner", model="gpt-4o", api_key="k")
        self.assertEqual(cfg.model, "gpt-4o")
        self.assertTrue(cfg.read_only)

    def test_all_roles_have_profiles(self):
        for role in ROLE_PROFILES:
            cfg = AgentConfig.from_role(role)
            cfg.validate()  # every profile must produce a valid config


if __name__ == "__main__":
    unittest.main(verbosity=2)
