"""The contract every Munder Difflin specialist implements."""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from emonphenom.requests_nl import ParsedRequest


@dataclass(frozen=True)
class AgentResult:
    agent: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    ok: bool = True

    def __str__(self) -> str:
        mark = "" if self.ok else "[!] "
        return f"{mark}{self.agent}: {self.summary}"


class Agent(ABC):
    """A specialist that owns one area of the business.

    Agents never talk to each other -- the orchestrator routes between them.
    That keeps each one independently testable and stops the web of
    cross-calls that makes multi-agent systems impossible to reason about.
    """

    name: str = "agent"
    intents: frozenset[str] = frozenset()

    def handles(self, intent: str) -> bool:
        return intent in self.intents

    @abstractmethod
    def run(self, conn: sqlite3.Connection, request: ParsedRequest) -> AgentResult:
        ...

    def _ok(self, summary: str, **data: Any) -> AgentResult:
        return AgentResult(self.name, summary, data, ok=True)

    def _fail(self, summary: str, **data: Any) -> AgentResult:
        return AgentResult(self.name, summary, data, ok=False)
