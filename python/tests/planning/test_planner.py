"""Planner tests — deterministic decomposition for every requirement kind,
LLM planner with a mocked model, JSON robustness, and acceptance: a complex
feature yields a valid DAG."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.planning import Planner, PlannerError, RequirementKind  # noqa: E402
from mini_claude.planning.requirement import Requirement  # noqa: E402
from mini_claude.planning.task import TaskStatus  # noqa: E402

GOOD_SPEC = {
    "tasks": [
        {"id": "T001", "title": "explore code", "description": "find call sites",
         "agent_role": "explorer", "dependencies": [], "priority": 5,
         "files": ["app/core.py"]},
        {"id": "T002", "title": "implement", "description": "write it",
         "agent_role": "coder", "dependencies": ["T001"], "priority": 3,
         "files": ["app/core.py"], "budget": {"max_cost_usd": 0.5, "max_turns": 20}},
        {"id": "T003", "title": "verify", "description": "run tests",
         "agent_role": "tester", "dependencies": ["T002"], "priority": 3},
        {"id": "T004", "title": "review", "description": "review diff",
         "agent_role": "reviewer", "dependencies": ["T003"], "priority": 1},
    ]
}


class TestDeterministicPlanner(unittest.TestCase):
    def setUp(self):
        self.planner = Planner()

    def _plan_kind(self, kind):
        return self.planner.plan(Requirement(
            kind=kind, title="t", description="d", source="s"))

    def test_feature_chain(self):
        dag = self._plan_kind(RequirementKind.FEATURE)
        self.assertEqual([t.agent_role for t in dag], ["coder", "tester", "reviewer"])
        self.assertEqual(dag.dependencies_of("T-F-2"), ["T-F-1"])
        self.assertEqual(dag.dependencies_of("T-F-3"), ["T-F-2"])

    def test_bug_chain(self):
        dag = self._plan_kind(RequirementKind.BUG)
        self.assertEqual([t.agent_role for t in dag], ["coder", "tester"])

    def test_refactor_chain(self):
        dag = self._plan_kind(RequirementKind.REFACTOR)
        self.assertEqual([t.agent_role for t in dag], ["coder", "tester"])

    def test_test_single(self):
        dag = self._plan_kind(RequirementKind.TEST)
        self.assertEqual([t.agent_role for t in dag], ["tester"])

    def test_documentation_single(self):
        dag = self._plan_kind(RequirementKind.DOCUMENTATION)
        self.assertEqual([t.agent_role for t in dag], ["coder"])

    def test_every_kind_produces_valid_dag(self):
        for kind in RequirementKind:
            dag = self._plan_kind(kind)
            report = dag.validate()
            self.assertTrue(report.valid, f"{kind} plan invalid: {report.errors}")
            dag.topological_order()

    def test_complex_feature_acceptance(self):
        """验收：复杂 Feature 生成合法的 DAG（链式依赖 + 角色 + 拓扑序）。"""
        req = Requirement(
            kind=RequirementKind.FEATURE,
            title="Multi-user auth",
            description="Login with JWT tokens and password hashing.",
            source="issue body",
            related_files=["app/auth.py", "app/models.py"],
        )
        dag = self.planner.plan(req)
        self.assertTrue(dag.validate().valid)
        order = dag.topological_order()
        self.assertEqual(len(order), 3)
        self.assertLess(order.index("T-F-1"), order.index("T-F-2"))
        self.assertLess(order.index("T-F-2"), order.index("T-F-3"))
        self.assertEqual(dag.get("T-F-1").files, ["app/auth.py", "app/models.py"])

    def test_plans_from_raw_string(self):
        dag = self.planner.plan("fix the broken login form")
        self.assertEqual(dag.get("T-B-1").agent_role, "coder")


class TestLLMPlanner(unittest.IsolatedAsyncioTestCase):
    def _planner(self, response: str) -> Planner:
        async def fake_llm(system: str, user: str) -> str:
            fake_llm.calls.append((system, user))
            return response

        fake_llm.calls = []
        return Planner(fake_llm)

    async def test_mocked_llm_plan(self):
        import json

        planner = self._planner(json.dumps(GOOD_SPEC))
        req = Requirement(kind=RequirementKind.FEATURE, title="t", description="d", source="s")
        dag = await planner.plan_with_llm(req)
        self.assertEqual(len(dag), 4)
        self.assertTrue(dag.validate().valid)
        self.assertEqual(dag.get("T002").agent_role, "coder")
        self.assertEqual(dag.get("T002").budget.max_cost_usd, 0.5)
        self.assertEqual(dag.get("T004").dependencies, ["T003"])
        # prompt sanity: system carries the schema, user carries the requirement
        system, user = planner._llm_call.calls[0]  # type: ignore[attr-defined]
        self.assertIn("tasks", system)
        self.assertIn("t", user)

    async def test_llm_prose_wrapped_json(self):
        planner = self._planner(
            'Here is your plan:\n```json\n' + __import__("json").dumps(GOOD_SPEC) + "\n```"
        )
        dag = await planner.plan_with_llm(Requirement(
            kind=RequirementKind.FEATURE, title="t", description="d", source="s"))
        self.assertEqual(len(dag), 4)

    async def test_no_json_raises(self):
        planner = self._planner("I cannot do this right now.")
        with self.assertRaises(PlannerError):
            await planner.plan_with_llm(Requirement(
                kind=RequirementKind.FEATURE, title="t", description="d", source="s"))

    async def test_cycle_in_llm_output_rejected(self):
        spec = {
            "tasks": [
                {"id": "A", "title": "a", "description": "", "agent_role": "coder",
                 "dependencies": ["B"]},
                {"id": "B", "title": "b", "description": "", "agent_role": "coder",
                 "dependencies": ["A"]},
            ]
        }
        planner = self._planner(__import__("json").dumps(spec))
        with self.assertRaises(PlannerError):
            await planner.plan_with_llm(Requirement(
                kind=RequirementKind.FEATURE, title="t", description="d", source="s"))

    async def test_missing_dependency_in_llm_output_rejected(self):
        spec = {"tasks": [
            {"id": "A", "title": "a", "description": "", "agent_role": "coder",
             "dependencies": ["GHOST"]},
        ]}
        planner = self._planner(__import__("json").dumps(spec))
        with self.assertRaises(PlannerError):
            await planner.plan_with_llm(Requirement(
                kind=RequirementKind.FEATURE, title="t", description="d", source="s"))

    async def test_bad_role_in_llm_output_rejected(self):
        spec = {"tasks": [
            {"id": "A", "title": "a", "description": "", "agent_role": "manager"},
        ]}
        planner = self._planner(__import__("json").dumps(spec))
        with self.assertRaises(PlannerError):
            await planner.plan_with_llm(Requirement(
                kind=RequirementKind.FEATURE, title="t", description="d", source="s"))

    async def test_no_llm_configured(self):
        with self.assertRaises(PlannerError):
            await Planner().plan_with_llm(Requirement(
                kind=RequirementKind.FEATURE, title="t", description="d", source="s"))

    async def test_extract_json_static(self):
        self.assertEqual(Planner._extract_json('{"tasks": []}'), {"tasks": []})
        with self.assertRaises(PlannerError):
            Planner._extract_json("no json here")


if __name__ == "__main__":
    unittest.main(verbosity=2)
