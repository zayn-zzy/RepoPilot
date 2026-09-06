"""Dependency graph — file- and module-level import relationships, built with
networkx. Edges point from importer to imported (dependency direction), so
`dependents()` (reverse edges) answers "who imports X"."""

from __future__ import annotations

import networkx as nx

from .symbols import ImportInfo


class DependencyGraph:
    def __init__(self) -> None:
        self._file_graph = nx.DiGraph()      # nodes: file paths
        self._module_graph = nx.DiGraph()    # nodes: dotted module names
        self._module_to_file: dict[str, str] = {}
        self._file_to_module: dict[str, str] = {}

    # ─── Construction ────────────────────────────────────────

    def add_file(self, file_path: str, module_name: str | None) -> None:
        self._file_graph.add_node(file_path)
        if module_name:
            self._module_to_file[module_name] = file_path
            self._file_to_module[file_path] = module_name
            self._module_graph.add_node(module_name)

    def remove_file(self, file_path: str) -> None:
        """Drop a file's nodes and the edges originating from it. Tolerates
        files that were never registered (idempotent)."""
        module_name = self._file_to_module.pop(file_path, None)
        if module_name and self._module_to_file.get(module_name) == file_path:
            del self._module_to_file[module_name]
        if self._file_graph.has_node(file_path):
            self._file_graph.remove_node(file_path)
        # The importer's module node loses its outgoing edges; keep the node
        # only while other files still import it (or it maps to a file).
        for node in (module_name, file_path):
            if node and self._module_graph.has_node(node):
                self._module_graph.remove_edges_from(list(self._module_graph.out_edges(node)))
                if self._module_graph.degree(node) == 0 and self._module_to_file.get(node) is None:
                    self._module_graph.remove_node(node)

    def add_import(self, from_file: str, imp: ImportInfo) -> str | None:
        """Record one import edge. Resolves the imported module to a file
        when possible; returns the resolved file path (None if unresolvable).
        External modules (stdlib/third-party) become module nodes without a
        file — still visible in module_dependencies()."""
        target_module = self._resolve_module_name(from_file, imp)
        if target_module is None:
            return None

        if target_module not in self._module_graph:
            self._module_graph.add_node(target_module)
        self._module_graph.add_edge(
            self._file_to_module.get(from_file, from_file), target_module,
            symbol=imp.symbol, alias=imp.alias,
        )

        target_file = self._module_to_file.get(target_module)
        if target_file is not None:
            if not self._file_graph.has_edge(from_file, target_file):
                self._file_graph.add_edge(from_file, target_file)
            return target_file
        return None

    def _resolve_module_name(self, from_file: str, imp: ImportInfo) -> str | None:
        if imp.level == 0:
            return imp.module or None
        # Relative: Python resolves `from .X` against the file's PACKAGE,
        # stepping up one level per extra dot. For an __init__.py the module
        # name IS the package; for pkg/sub/helper.py the package is pkg.sub.
        base = self._file_to_module.get(from_file)
        if not base:
            return None  # no module identity — relative imports can't resolve
        base_parts = base.split(".")
        if from_file.endswith("__init__.py"):
            package_parts = base_parts
        else:
            package_parts = base_parts[:-1]
        up = imp.level - 1
        if up >= len(package_parts):
            return None  # at or above the top-level package
        parts = package_parts[:len(package_parts) - up]
        if imp.module:
            parts = parts + imp.module.split(".")
        return ".".join(parts)

    # ─── Queries ─────────────────────────────────────────────

    def file_dependencies(self, file_path: str) -> list[str]:
        """Files this file imports (direct edges), sorted."""
        return sorted(nx.neighbors(self._file_graph, file_path)) if file_path in self._file_graph else []

    def module_dependencies(self, module: str) -> list[str]:
        """Modules this module imports — includes external modules and
        unresolvable internal ones, with importer-side edges only."""
        if module not in self._module_graph:
            return []
        return sorted(self._module_graph.successors(module))

    def dependents(self, file_path: str) -> list[str]:
        """Files that import this file, sorted."""
        return sorted(self._file_graph.predecessors(file_path)) if file_path in self._file_graph else []

    def importers_of(self, module: str) -> list[str]:
        if module not in self._module_graph:
            return []
        return sorted(self._module_graph.predecessors(module))

    def files(self) -> list[str]:
        return sorted(self._file_graph.nodes)

    def modules(self) -> list[str]:
        return sorted(self._module_graph.nodes)

    def file_of_module(self, module: str) -> str | None:
        return self._module_to_file.get(module)

    def module_of_file(self, file_path: str) -> str | None:
        return self._file_to_module.get(file_path)

    # ─── Structural queries ──────────────────────────────────

    def topological_order(self) -> list[str]:
        """Deterministic dependency-first order of files: every file appears
        after the files it imports (edges point importer → imported, so we
        sort the reversed graph). For scheduler use in later phases."""
        if not nx.is_directed_acyclic_graph(self._file_graph):
            raise ValueError("file dependency graph contains a cycle")
        return list(nx.lexicographical_topological_sort(self._file_graph.reverse(), key=lambda n: (n,)))

    def find_cycle(self) -> list[str] | None:
        """One cycle among files (import loop), or None."""
        try:
            return nx.find_cycle(self._file_graph)
        except nx.NetworkXNoCycle:
            return None

    def has_cycle(self) -> bool:
        return not nx.is_directed_acyclic_graph(self._file_graph)

    def closure(self, file_path: str) -> list[str]:
        """All files reachable from this file (transitive imports), sorted."""
        return sorted(nx.descendants(self._file_graph, file_path)) if file_path in self._file_graph else []
