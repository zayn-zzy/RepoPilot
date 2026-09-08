"""Baseline configurations and the ablation matrix.

    A: Original Mini Coding Agent + Grep + Read + Edit
    B: + Semantic Retrieval
    C: + Hybrid Retrieval (lexical + semantic)
    Proposed: + Structural Retrieval + Task DAG + Multi-Agent + Verification

Ablations (per the spec): Full, -Structural, -Semantic, -TaskDAG,
-Reviewer, -SelfRepair, -RepositoryMemory. Two of them (-TaskDAG,
-RepositoryMemory) are recorded as equivalent-to-Full by construction:
the Proposed stack does not yet wire the TaskDAG scheduler or any
cross-task repository memory (documented Phase 5/6 limitations), so
removing them changes nothing — recorded honestly in the results.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# A single-agent baseline's tool set (the original agent's tool names).
BASELINE_TOOLS: dict[str, frozenset[str]] = {
    "A": frozenset({"grep_search", "read_file", "edit_file"}),
    "B": frozenset({"grep_search", "read_file", "edit_file", "semantic_search"}),
    "C": frozenset({"grep_search", "read_file", "edit_file",
                    "semantic_search", "symbol_search", "dependency_search"}),
}


@dataclass(frozen=True)
class BaselineConfig:
    name: str
    kind: str = "single"                 # "single" | "team"
    tools: frozenset[str] = field(default_factory=frozenset)
    semantic: bool = False               # semantic retrieval on
    hybrid: bool = False                 # lexical+semantic retrieval on
    structural: bool = False             # structural retrieval on
    task_dag: bool = False               # not wired in the Proposed stack
    reviewer: bool = True                # team reviewer stage on
    self_repair: bool = True             # verification + repair loop on
    repo_memory: bool = False            # not wired in the Proposed stack
    team: bool = False                   # alias of kind for readability
    description: str = ""

    @property
    def retrieval_stack(self) -> str:
        """Which retrieval stack this config uses (for retrieval eval):
        grep | semantic | hybrid | hybrid+structural."""
        if self.structural and self.hybrid:
            return "hybrid+structural"
        if self.hybrid:
            return "hybrid"
        if self.semantic:
            return "semantic"
        return "grep"


def _mk(name: str, kind: str, *, tools: frozenset[str] | None = None,
        semantic: bool = False, hybrid: bool = False, structural: bool = False,
        reviewer: bool = True, self_repair: bool = True,
        description: str = "") -> BaselineConfig:
    return BaselineConfig(
        name=name, kind=kind, tools=tools or frozenset(),
        semantic=semantic, hybrid=hybrid, structural=structural,
        reviewer=reviewer, self_repair=self_repair,
        team=(kind == "team"), description=description,
    )


BASELINES: dict[str, BaselineConfig] = {
    "A": _mk("A", "single", tools=BASELINE_TOOLS["A"],
             description="Original Mini Coding Agent + Grep + Read + Edit"),
    "B": _mk("B", "single", tools=BASELINE_TOOLS["B"], semantic=True,
             description="Baseline A + Semantic Retrieval"),
    "C": _mk("C", "single", tools=BASELINE_TOOLS["C"], semantic=True, hybrid=True,
             description="Baseline B + Hybrid (lexical+semantic) Retrieval"),
    "Proposed": _mk("Proposed", "team", semantic=True, hybrid=True, structural=True,
                    description="+ Structural Retrieval + Task DAG* + Multi-Agent "
                                "+ Verification (*Task DAG not yet wired — see "
                                "DEVELOPMENT_RESULTS.md Phase 5)"),
}

# The ablation matrix over the Proposed stack.
ABLATIONS: dict[str, BaselineConfig] = {
    "Full": BASELINES["Proposed"],
    "-Structural Retrieval": replace(BASELINES["Proposed"], name="-Structural",
                                     structural=False),
    "-Semantic Retrieval": replace(BASELINES["Proposed"], name="-Semantic",
                                   semantic=False, hybrid=False),
    "-Task DAG": replace(BASELINES["Proposed"], name="-TaskDAG", task_dag=False,
                         description="equivalent to Full by construction: the "
                                     "TaskDAG scheduler is not yet wired into the "
                                     "Proposed stack (documented limitation)"),
    "-Reviewer": replace(BASELINES["Proposed"], name="-Reviewer", reviewer=False),
    "-Self-Repair": replace(BASELINES["Proposed"], name="-SelfRepair",
                            self_repair=False),
    "-Repository Memory": replace(BASELINES["Proposed"], name="-Memory",
                                  repo_memory=False,
                                  description="equivalent to Full by construction: "
                                              "no cross-task repository memory is "
                                              "wired yet (documented limitation)"),
}
