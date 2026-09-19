import pytest

from emonphenom import fulfillment as ful
from emonphenom import inventory as inv
from emonphenom import ledger
from emonphenom.db import OPENING_CASH_CENTS
from emonphenom.pricing import build_quote


def test_covered_order_reserves_stock(conn):
    quote = build_quote("Q-1", "Scranton SD", [("A4-COPY-80", 500)])
    order = ful.place_order(conn, quote)
    assert order.status == ful.RESERVED
    assert order.shortfalls == ()
    assert inv.stock(conn, "A4-COPY-80").reserved == 500


def test_short_order_reserves_nothing_and_raises_a_purchase_order(conn):
    quote = build_quote("Q-1", "Vance Refrigeration", [("BANNER-36", 50)])
    order = ful.place_order(conn, quote)

    assert order.status == ful.AWAITING_STOCK
    assert [s.sku for s in order.shortfalls] == ["BANNER-36"]
    assert order.shortfalls[0].short_by == 32
    # all-or-nothing: no stock is locked up by an order that cannot ship
    assert inv.stock(conn, "BANNER-36").reserved == 0

    pos = ful.open_restocks(conn)
    assert len(pos) == 1
    assert pos[0]["sku"] == "BANNER-36"
    assert pos[0]["quantity"] == 60  # reorder qty, which covers the 32 shortfall


def test_a_partially_covered_multiline_order_holds_no_stock(conn):
    quote = build_quote("Q-1", "Dunder Co", [("A4-COPY-80", 100), ("BANNER-36", 500)])
    order = ful.place_order(conn, quote)
    assert order.status == ful.AWAITING_STOCK
    assert inv.stock(conn, "A4-COPY-80").reserved == 0


def test_fulfilling_ships_stock_and_books_revenue(conn):
    quote = build_quote("Q-1", "Scranton SD", [("LTR-COPY-20", 1000)])
    order = ful.place_order(conn, quote)
    shipped = ful.fulfil(conn, order.id)

    assert shipped.status == ful.FULFILLED
    level = inv.stock(conn, "LTR-COPY-20")
    assert level.on_hand == 1400
    assert level.reserved == 0
    assert ledger.cash_balance(conn) == OPENING_CASH_CENTS + quote.total_cents


def test_cannot_ship_an_order_awaiting_stock(conn):
    quote = build_quote("Q-1", "Vance", [("BANNER-36", 50)])
    order = ful.place_order(conn, quote)
    with pytest.raises(ValueError, match="awaiting_stock"):
        ful.fulfil(conn, order.id)


def test_cancelling_releases_the_reservation(conn):
    quote = build_quote("Q-1", "Scranton SD", [("A4-COPY-80", 400)])
    order = ful.place_order(conn, quote)
    ful.cancel(conn, order.id)
    assert inv.stock(conn, "A4-COPY-80").reserved == 0
    assert ful.get_order(conn, order.id).status == ful.CANCELLED


def test_receiving_a_delivery_costs_cash_and_adds_stock(conn):
    po_id = ful.raise_restock(conn, "BANNER-36", 60)
    ful.receive_restock(conn, po_id)
    assert inv.stock(conn, "BANNER-36").on_hand == 78
    expected = OPENING_CASH_CENTS - (3120 * 60)
    assert ledger.cash_balance(conn) == expected


def test_a_delivery_cannot_be_received_twice(conn):
    po_id = ful.raise_restock(conn, "BANNER-36", 60)
    ful.receive_restock(conn, po_id)
    with pytest.raises(ValueError, match="already received"):
        ful.receive_restock(conn, po_id)


def test_backorder_is_promoted_once_stock_lands(conn):
    quote = build_quote("Q-1", "Vance", [("BANNER-36", 50)])
    order = ful.place_order(conn, quote)
    assert order.status == ful.AWAITING_STOCK

    for po in ful.open_restocks(conn):
        ful.receive_restock(conn, po["id"])

    assert ful.retry_awaiting(conn) == [order.id]
    assert ful.get_order(conn, order.id).status == ful.RESERVED
    assert inv.stock(conn, "BANNER-36").reserved == 50
    ful.fulfil(conn, order.id)
