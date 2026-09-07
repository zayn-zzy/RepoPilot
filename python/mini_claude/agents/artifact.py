"""AgentArtifact — the ONLY channel between agents.

Agents never chat freely; each role publishes a structured artifact and the
orchestrator hands artifacts to the next role as JSON. The mailbox keeps the
pipeline auditable and the payload schemas inspectable."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

ARTIFACT_KINDS = ("plan", "exploration", "code_change", "test_report", "review")


class ArtifactError(ValueError):
    pass


@dataclass
class AgentArtifact:
    kind: str
    producer: str
    payload: dict
    artifact_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ARTIFACT_KINDS:
            raise ArtifactError(
                f"unknown artifact kind {self.kind!r} (expected one of {ARTIFACT_KINDS})"
            )
        if not self.producer.strip():
            raise ArtifactError("producer must be non-empty")
        if not isinstance(self.payload, dict):
            raise ArtifactError("payload must be a dict")
        if not self.artifact_id:
            self.artifact_id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "producer": self.producer,
            "created_at": self.created_at,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentArtifact":
        return cls(
            kind=str(data["kind"]),
            producer=str(data["producer"]),
            payload=dict(data.get("payload") or {}),
            artifact_id=str(data.get("artifact_id") or ""),
            created_at=str(data.get("created_at") or ""),
        )


class ArtifactMailbox:
    """Ordered store of published artifacts; `latest(kind)` returns the most
    recent artifact of a kind (the pipeline uses each kind exactly once)."""

    def __init__(self) -> None:
        self._artifacts: list[AgentArtifact] = []

    def publish(self, artifact: AgentArtifact) -> AgentArtifact:
        self._artifacts.append(artifact)
        return artifact

    def latest(self, kind: str) -> AgentArtifact | None:
        for a in reversed(self._artifacts):
            if a.kind == kind:
                return a
        return None

    def all(self) -> list[AgentArtifact]:
        return list(self._artifacts)

    def to_json(self) -> str:
        import json

        return json.dumps([a.to_dict() for a in self._artifacts], ensure_ascii=False, indent=2)

    def __len__(self) -> int:
        return len(self._artifacts)
