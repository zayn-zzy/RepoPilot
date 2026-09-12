"""GraphService — module dependency graph queries (Web Phase 1).

Implements the §18 loading discipline on top of the existing module
graph: root/depth/limit — a large graph is never shipped whole."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .repository import _repopilot_dir


@dataclass
class GraphResult:
    index_note: str = ""
    modules: list[str] = field(default_factory=list)
    edges: int = 0
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    nodes: list[dict] = field(default_factory=list)   # {"id", "kind"}
    links: list[dict] = field(default_factory=list)   # {"source", "target"}
    truncated: bool = False


class GraphService:
    def graph(self, path: str | Path, *,
              root: str | None = None,
              depth: int | None = None,
              limit: int | None = None,
              ) -> GraphResult:
        """The module dependency graph, optionally restricted to a
        ``root`` module's neighborhood (BFS up to ``depth`` hops,
        capped at ``limit`` nodes). Without restrictions the whole
        graph is returned (the CLI's current behavior)."""
        from ..repo import RepositoryIndex
        repo_root = Path(path).resolve()
        index = RepositoryIndex(repo_root)
        _, index_note = index.load_or_build(
            _repopilot_dir(repo_root) / "index.db")
        modules = index.graph.modules()
        deps: dict[str, list[str]] = {
            m: index.module_dependencies(m) for m in modules
        }

        selected = set(modules)
        truncated = False
        if root is not None or depth is not None or limit is not None:
            if root is not None and root not in deps:
                from .errors import ApplicationError
                raise ApplicationError(
                    "MODULE_NOT_FOUND",
                    f"module {root!r} is not in the index")
            start = root
            frontier = [start] if start is not None else list(modules)
            hops = depth if depth is not None else 1
            selected = set(frontier)
            for _ in range(hops):
                nxt: list[str] = []
                for m in frontier:
                    for d in deps.get(m, []):
                        if d not in selected:
                            nxt.append(d)
                frontier = list(dict.fromkeys(nxt))
                selected.update(frontier)
            if limit is not None and len(selected) > limit:
                selected = set(sorted(selected)[:limit])
                truncated = True

        nodes = [{"id": m, "kind": "module"}
                 for m in sorted(selected)]
        links = [{"source": m, "target": d}
                 for m in selected for d in deps.get(m, [])
                 if d in selected]
        return GraphResult(
            index_note=index_note,
            modules=sorted(selected), edges=len(links),
            dependencies={m: [d for d in deps.get(m, []) if d in selected]
                          for m in selected},
            nodes=nodes, links=links, truncated=truncated)
