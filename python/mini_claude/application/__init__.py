"""Application Services — the use-case layer between RepoPilot Core
and its two consumers (CLI + Web API), per the Web productization
spec §9: services never print, return structured results, and both
fronts call the same implementation (§2.1 — one logic, never two)."""

from .ask import AskHit, AskResult, AskService
from .benchmark import BenchmarkResult, BenchmarkService
from .errors import ApplicationError
from .graph import GraphResult, GraphService
from .planning import PlanResult, PlanningService, plan_to_web
from .registry import RegistryContext, RepositoryRegistry
from .repository import (IndexResult, IndexStatus, InitResult,
                         RepositoryService)
from .run import RunResult, RunService

__all__ = [
    "ApplicationError",
    "AskHit", "AskResult", "AskService",
    "BenchmarkResult", "BenchmarkService",
    "GraphResult", "GraphService",
    "PlanResult", "PlanningService", "plan_to_web",
    "IndexResult", "IndexStatus", "InitResult", "RepositoryService",
    "RegistryContext", "RepositoryRegistry",
    "RunResult", "RunService",
]
