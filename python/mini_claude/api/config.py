"""API configuration (Web Phase 2) — env-driven settings with honest
defaults. The workspace root is the security boundary from §31:
repositories served by the API must live under it."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "0.1.0"


@dataclass
class Settings:
    workspace_root: Path
    dev_mode: bool = False
    db_url: str = ""
    cors_origins: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls, environ: dict | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        root = Path(env.get("REPOPILOT_WORKSPACE_ROOT") or os.getcwd()).resolve()
        dev = (env.get("REPOPILOT_DEV_MODE") or env.get("DEV_MODE") or "").lower() in (
            "1", "true", "yes")
        db_url = env.get("REPOPILOT_DB_URL") or str(root / ".repopilot" / "app.db")
        origins = [o.strip() for o in
                   (env.get("REPOPILOT_CORS_ORIGINS") or
                    "http://localhost:5173").split(",") if o.strip()]
        return cls(workspace_root=root, dev_mode=dev,
                   db_url=db_url, cors_origins=origins)
