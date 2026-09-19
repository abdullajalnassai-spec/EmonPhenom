"""The four specialists: catalog, inventory, quoting, fulfillment, finance."""

from __future__ import annotations

import sqlite3

from emonphenom import fulfillment, inventory, ledger
from emonphenom.agents.base import Agent, AgentResult
from emonphenom.catalog import UnknownSKU, by_sku, search
from emonphenom.money import fmt
from emonphenom.pricing import build_quote
from emonphenom.requests_nl import (
    CATALOG_LOOKUP,
    FINANCE,
    FULFIL,
    ORDER,
    QUOTE,
    STOCK,
    ParsedRequest,
    match_product,
)


class CatalogAgent(Agent):
    name = "catalog"
    intents = frozenset({CATALOG_LOOKUP})

    def run(self, conn: sqlite3.Connection, request: ParsedRequest) -> AgentResult:
        hits = search(request.raw_text) if request.raw_text else []
        if not hits:
            product = match_product(request.raw_text or "")
            hits = [product] if product else []
        if not hits:
            return self._fail(
                "nothing in the catalog matches that", query=request.raw_text
            )
        listing = [
            f"{p.sku:<13} {p.name:<32} {fmt(p.list_price_cents)}/{p.unit}" for p in hits[:8]
        ]
        return self._ok(
            f"{len(hits)} catalog match(es)",
            skus=[p.sku for p in hits],
            listing=listing,
        )


class InventoryAgent(Agent):
    name = "inventory"
    intents = frozenset({STOCK})

    def run(self, conn: sqlite3.Connection, request: ParsedRequest) -> AgentResult:
        sku = request.sku
        if sku is None:
            product = match_product(request.raw_text or "")
            sku = product.sku if product else None

        if sku is None:
            low = inventory.below_reorder(conn)
            return self._ok(
                f"{len(low)} SKU(s) at or below reorder point",
                below_reorder=[s.sku for s in low],
            )
        try:
            level = inventory.stock(conn, sku)
        except (UnknownSKU, KeyError):
            return self._fail(f"{sku} is not a stocked SKU", sku=sku)

        product = by_sku(level.sku)
        flag = " (below reorder point)" if level.below_reorder_point else ""
        return self._ok(
            f"{level.available:,} {product.unit}(s) of {level.sku} available "
            f"({level.on_hand:,} on hand, {level.reserved:,} reserved){flag}",
            sku=level.sku,
            available=level.available,
            on_hand=level.on_hand,
            reserved=level.reserved,
            below_reorder_point=level.below_reorder_point,
        )


class QuotingAgent(Agent):
    name = "quoting"
    intents = frozenset({QUOTE})

    def run(self, conn: sqlite3.Connection, request: ParsedRequest) -> AgentResult:
        if not request.items:
            return self._fail("no catalog items recognised in that enquiry")
        quote_no = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"] + 1
        try:
            quote = build_quote(
                f"Q-{quote_no:04d}",
                request.customer,
                list(request.items),
                rush=request.rush,
                rep_discount_pct=request.rep_discount_pct,
            )
        except (UnknownSKU, ValueError) as exc:
            return self._fail(str(exc))

        clamped = [l.sku for l in quote.lines if l.margin_floor_applied]
        summary = (
            f"{quote.quote_id} for {request.customer}: {fmt(quote.total_cents)} "
            f"across {len(quote.lines)} line(s), margin {quote.margin_pct:.1f}%"
        )
        if clamped:
            summary += f" -- margin floor held on {', '.join(clamped)}"
        return self._ok(summary, quote=quote, margin_floor_applied=clamped)


class FulfillmentAgent(Agent):
    name = "fulfillment"
    intents = frozenset({ORDER, FULFIL})

    def run(self, conn: sqlite3.Connection, request: ParsedRequest) -> AgentResult:
        if request.intent == FULFIL:
            if not request.order_id:
                return self._fail("no order number in that request")
            try:
                order = fulfillment.fulfil(conn, request.order_id)
            except (KeyError, ValueError) as exc:
                return self._fail(str(exc), order_id=request.order_id)
            return self._ok(
                f"{order.id} shipped, {fmt(order.total_cents)} booked to revenue",
                order_id=order.id,
                total_cents=order.total_cents,
            )

        if not request.items:
            return self._fail("no catalog items recognised in that order")
        quote_no = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"] + 1
        try:
            quote = build_quote(
                f"Q-{quote_no:04d}", request.customer, list(request.items),
                rush=request.rush, rep_discount_pct=request.rep_discount_pct,
            )
        except (UnknownSKU, ValueError) as exc:
            return self._fail(str(exc))

        order = fulfillment.place_order(conn, quote)
        if order.shortfalls:
            shorts = ", ".join(
                f"{s.sku} short by {s.short_by:,}" for s in order.shortfalls
            )
            return self._ok(
                f"{order.id} taken but awaiting stock ({shorts}); purchase orders raised",
                order_id=order.id,
                status=order.status,
                shortfalls=[s.sku for s in order.shortfalls],
            )
        return self._ok(
            f"{order.id} confirmed, {fmt(order.total_cents)}, stock reserved",
            order_id=order.id,
            status=order.status,
            total_cents=order.total_cents,
        )


class FinanceAgent(Agent):
    name = "finance"
    intents = frozenset({FINANCE})

    def run(self, conn: sqlite3.Connection, request: ParsedRequest) -> AgentResult:
        rep = ledger.report(conn)
        return self._ok(
            f"cash {fmt(rep.cash_cents)}, revenue {fmt(rep.revenue_cents)}, "
            f"margin {rep.gross_margin_pct:.1f}%, "
            f"{rep.orders_fulfilled} order(s) shipped",
            report=rep,
        )
