"""Routing layer.

The orchestrator owns exactly two responsibilities: turn text into a structured
request, and hand that request to the one specialist that claims its intent. It
holds no business logic of its own, so a bug is always in a named specialist
rather than in a tangle of routing conditionals.
"""

from __future__ import annotations

import sqlite3

from emonphenom.agents.base import Agent, AgentResult
from emonphenom.agents.specialists import (
    CatalogAgent,
    FinanceAgent,
    FulfillmentAgent,
    InventoryAgent,
    QuotingAgent,
)
from emonphenom.requests_nl import ParsedRequest, parse


def default_agents() -> tuple[Agent, ...]:
    return (
        QuotingAgent(),
        InventoryAgent(),
        FulfillmentAgent(),
        FinanceAgent(),
        CatalogAgent(),
    )


class Orchestrator:
    def __init__(self, conn: sqlite3.Connection, agents: tuple[Agent, ...] | None = None):
        self.conn = conn
        self.agents = agents if agents is not None else default_agents()

    def route(self, intent: str) -> Agent | None:
        for agent in self.agents:
            if agent.handles(intent):
                return agent
        return None

    def dispatch(self, request: ParsedRequest) -> AgentResult:
        agent = self.route(request.intent)
        if agent is None:
            return AgentResult(
                "orchestrator", f"no agent handles intent {request.intent!r}", ok=False
            )
        return agent.run(self.conn, request)

    def ask(
        self, text: str, *, customer: str = "walk-in", prefer_claude: bool = True
    ) -> AgentResult:
        """Full path: free text -> structured request -> specialist."""
        request = parse(text, customer=customer, prefer_claude=prefer_claude)
        return self.dispatch(request)
