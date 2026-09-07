"""AgentArtifact / ArtifactMailbox tests — serialization, kind validation,
ordering and lookup."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.agents import AgentArtifact, ArtifactError, ArtifactMailbox  # noqa: E402


def _artifact(kind="plan", producer="planner", payload=None):
    return AgentArtifact(kind=kind, producer=producer, payload=payload or {})


class TestAgentArtifact(unittest.TestCase):
    def test_roundtrip_serialization(self):
        a = _artifact(kind="code_change", producer="coder",
                      payload={"files_modified": ["a.py"], "changes": ["x"]})
        data = a.to_dict()
        b = AgentArtifact.from_dict(data)
        self.assertEqual(b.kind, "code_change")
        self.assertEqual(b.producer, "coder")
        self.assertEqual(b.payload["files_modified"], ["a.py"])
        self.assertEqual(b.artifact_id, a.artifact_id)
        self.assertEqual(b.created_at, a.created_at)

    def test_all_five_kinds_valid(self):
        for kind in ("plan", "exploration", "code_change", "test_report", "review"):
            _artifact(kind=kind)

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ArtifactError):
            _artifact(kind="gossip")

    def test_empty_producer_rejected(self):
        with self.assertRaises(ArtifactError):
            _artifact(producer="  ")

    def test_non_dict_payload_rejected(self):
        with self.assertRaises(ArtifactError):
            _artifact(payload="not a dict")

    def test_ids_auto_generated_and_unique(self):
        a, b = _artifact(), _artifact()
        self.assertTrue(a.artifact_id and b.artifact_id)
        self.assertNotEqual(a.artifact_id, b.artifact_id)


class TestMailbox(unittest.TestCase):
    def test_publish_and_latest(self):
        mb = ArtifactMailbox()
        mb.publish(_artifact("plan", payload={"tasks": ["T1"]}))
        mb.publish(_artifact("plan", payload={"tasks": ["T2"]}))
        self.assertEqual(mb.latest("plan").payload["tasks"], ["T2"])

    def test_latest_missing_kind(self):
        mb = ArtifactMailbox()
        self.assertIsNone(mb.latest("review"))

    def test_all_preserves_order(self):
        mb = ArtifactMailbox()
        for kind in ("plan", "exploration", "code_change", "test_report", "review"):
            mb.publish(_artifact(kind))
        self.assertEqual([a.kind for a in mb.all()],
                         ["plan", "exploration", "code_change", "test_report", "review"])
        self.assertEqual(len(mb), 5)

    def test_to_json(self):
        mb = ArtifactMailbox()
        mb.publish(_artifact("plan", payload={"tasks": []}))
        import json

        parsed = json.loads(mb.to_json())
        self.assertEqual(parsed[0]["kind"], "plan")


if __name__ == "__main__":
    unittest.main(verbosity=2)
