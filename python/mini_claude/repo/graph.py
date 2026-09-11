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
        target_module = self.resolve_module(from_file, imp)
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

    def resolve_module(self, from_file: str, imp: ImportInfo) -> str | None:
        """Resolve an ImportInfo to a module name (public — the index
        reuses it for reference resolution). Python-relative imports
        resolve against the importer's package; JS-style relative
        specifiers ('./x', '../y') are normalized to their dotted
        equivalent; bare names pass through."""
        if imp.level == 0:
            if imp.module.startswith("./") or imp.module.startswith("../"):
                return self._resolve_js_specifier(from_file, imp.module)
            return imp.module or None
        return self._resolve_python_relative(from_file, imp)

    def _resolve_python_relative(self, from_file: str, imp: ImportInfo) -> str | None:
        # Python resolves `from .X` against the file's PACKAGE,
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

    # Backward-compatible alias (the pre-Phase-13 private name).
    def _resolve_module_name(self, from_file: str, imp: ImportInfo) -> str | None:
        return self.resolve_module(from_file, imp)

    def _resolve_js_specifier(self, from_file: str, specifier: str) -> str | None:
        # './x' → the importer's directory + x; '../y' steps up. Extension
        # and index files are the file-resolution layer's concern — this
        # returns the normalized dotted module path only.
        base = self._file_to_module.get(from_file)
        if not base:
            return None
        dir_parts = base.split(".")[:-1]
        parts = specifier.split("/")
        ups = 0
        while parts and parts[0] in (".", ".."):
            if parts[0] == "..":
                ups += 1
            parts = parts[1:]
        if ups >= len(dir_parts):
            return None
        return ".".join(dir_parts[:len(dir_parts) - ups] + parts)

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


class ReferenceGraph:
    """Symbol-level call/reference graph — the complement of the file-level
    import graph: edges run caller → target, where callers are qualified
    symbol names and targets are either resolved qualified names or the
    unresolved name as written (kept honestly, marked).

    Edge attributes: kind ("call" | "attribute"), count (aggregated
    duplicates), caller_file / target_file (for file-level aggregation).
    """

    def __init__(self) -> None:
        self._graph = nx.DiGraph()
        self._unresolved: set[str] = set()

    def add_reference(self, ref, resolved_target: str | None,
                      target_file: str | None) -> None:
        caller = ref.caller or ref.file_path   # module-level code
        target = resolved_target or ref.target
        if resolved_target is None:
            self._unresolved.add(ref.target)
        if not caller or not target:
            return
        if self._graph.has_edge(caller, target):
            self._graph[caller][target]["count"] += 1
            return
        self._graph.add_edge(
            caller, target, kind=ref.kind, count=1,
            caller_file=ref.file_path, target_file=target_file or "",
        )

    # ─── Queries ─────────────────────────────────────────────

    def callers_of(self, qualified_name: str) -> list[tuple[str, str, int]]:
        """(caller, kind, count) for every symbol that references this one."""
        if qualified_name not in self._graph:
            return []
        return sorted(
            (u, d["kind"], d["count"])
            for u, d in self._graph.pred[qualified_name].items())

    def callees_of(self, qualified_name: str) -> list[tuple[str, str, int]]:
        """(target, kind, count) for everything this symbol references."""
        if qualified_name not in self._graph:
            return []
        return sorted(
            (v, d["kind"], d["count"])
            for v, d in self._graph.succ[qualified_name].items())

    def unresolved(self) -> list[str]:
        """Reference targets that could not be resolved to a known symbol."""
        return sorted(self._unresolved)

    def callers_of_file(self, file_path: str) -> list[tuple[str, int]]:
        """(caller_file, count) aggregated over every reference whose
        target lives in this file."""
        out: dict[str, int] = {}
        for _u, _v, d in self._graph.edges(data=True):
            if d.get("target_file") == file_path:
                out[d["caller_file"]] = out.get(d["caller_file"], 0) + d["count"]
        return sorted(out.items(), key=lambda kv: -kv[1])

    def callees_in_file(self, file_path: str) -> list[tuple[str, int]]:
        """(target, count) aggregated over references originating in this
        file — resolved qualified names and unresolved names alike."""
        out: dict[str, int] = {}
        for _u, v, d in self._graph.edges(data=True):
            if d.get("caller_file") == file_path:
                out[v] = out.get(v, 0) + d["count"]
        return sorted(out.items(), key=lambda kv: -kv[1])

    def nodes(self) -> list[str]:
        return sorted(self._graph.nodes)

    def edges(self) -> list[tuple[str, str, str, int]]:
        """(caller, target, kind, count)."""
        return sorted(
            (u, v, d["kind"], d["count"])
            for u, v, d in self._graph.edges(data=True))
