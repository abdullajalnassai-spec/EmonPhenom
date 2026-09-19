"""The Munder Difflin agent layer."""

from emonphenom.agents.base import Agent, AgentResult
from emonphenom.agents.orchestrator import Orchestrator, default_agents
from emonphenom.agents.specialists import (
    CatalogAgent,
    FinanceAgent,
    FulfillmentAgent,
    InventoryAgent,
    QuotingAgent,
)

__all__ = [
    "Agent", "AgentResult", "Orchestrator", "default_agents",
    "CatalogAgent", "FinanceAgent", "FulfillmentAgent",
    "InventoryAgent", "QuotingAgent",
]
