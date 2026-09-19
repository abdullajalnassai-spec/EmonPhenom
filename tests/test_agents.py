import pytest

from emonphenom import fulfillment as ful
from emonphenom.agents import (
    FinanceAgent,
    FulfillmentAgent,
    InventoryAgent,
    Orchestrator,
    QuotingAgent,
)
from emonphenom.requests_nl import FINANCE, ORDER, QUOTE, STOCK, ParsedRequest


@pytest.fixture
def orch(conn):
    return Orchestrator(conn)


def test_routing_picks_the_owning_specialist(orch):
    assert isinstance(orch.route(QUOTE), QuotingAgent)
    assert isinstance(orch.route(STOCK), InventoryAgent)
    assert isinstance(orch.route(ORDER), FulfillmentAgent)
    assert isinstance(orch.route(FINANCE), FinanceAgent)
    assert orch.route("nonsense") is None


def test_unknown_intent_fails_loudly(orch):
    result = orch.dispatch(ParsedRequest(intent="nonsense"))
    assert result.ok is False
    assert "no agent handles" in result.summary


def test_quote_flows_end_to_end(orch):
    result = orch.ask("how much for 600 reams of A4 copy paper?",
                      customer="Scranton SD", prefer_claude=False)
    assert result.ok
    quote = result.data["quote"]
    assert quote.customer == "Scranton SD"
    assert [line.sku for line in quote.lines] == ["A4-COPY-80"]
    assert quote.lines[0].discount_pct == 10


def test_quoting_reports_the_margin_floor(orch):
    result = orch.dispatch(
        ParsedRequest(intent=QUOTE, customer="Big Account",
                      items=(("LTR-COPY-20", 1000),), rep_discount_pct=25)
    )
    assert result.data["margin_floor_applied"] == ["LTR-COPY-20"]
    assert "margin floor held" in result.summary


def test_quoting_without_recognisable_items_fails(orch):
    result = orch.ask("hello there", prefer_claude=False)
    assert result.ok is False


def test_order_then_ship(orch, conn):
    placed = orch.ask("we'll take 400 reams of letter copy paper",
                      customer="Scranton SD", prefer_claude=False)
    assert placed.ok and placed.data["status"] == ful.RESERVED
    order_id = placed.data["order_id"]

    shipped = orch.ask(f"ship {order_id}", prefer_claude=False)
    assert shipped.ok
    assert ful.get_order(conn, order_id).status == ful.FULFILLED


def test_order_beyond_stock_reports_the_backorder(orch):
    result = orch.ask("we'll take 500 rolls of banner stock",
                      customer="Vance", prefer_claude=False)
    assert result.ok
    assert result.data["status"] == ful.AWAITING_STOCK
    assert result.data["shortfalls"] == ["BANNER-36"]


def test_shipping_an_unknown_order_fails(orch):
    result = orch.ask("ship SO-9999", prefer_claude=False)
    assert result.ok is False


def test_inventory_agent_answers_for_a_named_product(orch):
    result = orch.ask("do you have banner stock available?", prefer_claude=False)
    assert result.ok
    assert result.data["sku"] == "BANNER-36"
    assert result.data["available"] == 18


def test_inventory_agent_without_a_product_lists_reorder_candidates(orch):
    result = orch.dispatch(ParsedRequest(intent=STOCK, raw_text=""))
    assert result.ok
    assert "ENV-A7-CRM" in result.data["below_reorder"]


def test_finance_agent_summarises_the_books(orch):
    result = orch.ask("how are we doing on cash?", prefer_claude=False)
    assert result.ok
    assert result.data["report"].cash_cents == 2_500_000


def test_catalog_agent_lists_matches(orch):
    result = orch.ask("tell me about envelopes", prefer_claude=False)
    assert result.ok
    assert "ENV-10-WHT" in result.data["skus"]
