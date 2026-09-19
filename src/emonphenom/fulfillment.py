"""Order lifecycle: quote -> order -> shipped, and the supplier restock loop.

Reservation is all-or-nothing. A partly-reserved order is the worst outcome for
a paper company: stock is locked up, the customer still cannot be served, and
nobody is told. So an order that cannot be fully covered holds no stock at all,
goes to `awaiting_stock`, and raises purchase orders for the shortfall.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from emonphenom import inventory as inv
from emonphenom import ledger
from emonphenom.catalog import by_sku
from emonphenom.pricing import Quote

RESERVED = "reserved"
AWAITING_STOCK = "awaiting_stock"
FULFILLED = "fulfilled"
CANCELLED = "cancelled"


@dataclass(frozen=True)
class Shortfall:
    sku: str
    wanted: int
    available: int

    @property
    def short_by(self) -> int:
        return self.wanted - self.available


@dataclass(frozen=True)
class OrderLine:
    sku: str
    quantity: int
    total_cents: int


@dataclass(frozen=True)
class Order:
    id: str
    customer: str
    status: str
    total_cents: int
    margin_cents: int
    placed_on: str
    lines: tuple[OrderLine, ...]
    shortfalls: tuple[Shortfall, ...] = ()

    @property
    def is_shippable(self) -> bool:
        return self.status == RESERVED


def _next_id(conn: sqlite3.Connection, table: str, prefix: str) -> str:
    n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    return f"{prefix}-{n + 1:04d}"


def raise_restock(
    conn: sqlite3.Connection, sku: str, quantity: int, *, on: date | None = None
) -> str:
    """Place a purchase order with the supplier. Cash moves on receipt, not now."""
    product = by_sku(sku)
    day = on or date.today()
    restock_id = _next_id(conn, "restocks", "PO")
    conn.execute(
        "INSERT INTO restocks (id, sku, quantity, cost_cents, ordered_on, expected_on)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            restock_id,
            product.sku,
            quantity,
            product.unit_cost_cents * quantity,
            day.isoformat(),
            (day + timedelta(days=product.lead_time_days)).isoformat(),
        ),
    )
    conn.commit()
    return restock_id


def receive_restock(
    conn: sqlite3.Connection, restock_id: str, *, on: date | None = None
) -> None:
    """Take delivery: stock goes up, cash goes down."""
    row = conn.execute("SELECT * FROM restocks WHERE id = ?", (restock_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such restock {restock_id}")
    if row["received"]:
        raise ValueError(f"{restock_id} was already received")
    inv.receive(conn, row["sku"], row["quantity"])
    ledger.record(conn, ledger.SUPPLIER_PURCHASE, -row["cost_cents"], restock_id, on=on)
    conn.execute("UPDATE restocks SET received = 1 WHERE id = ?", (restock_id,))
    conn.commit()


def open_restocks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM restocks WHERE received = 0 ORDER BY expected_on"
    ).fetchall()


def has_open_restock(conn: sqlite3.Connection, sku: str) -> bool:
    """True if stock is already on order -- stops duplicate purchase orders."""
    row = conn.execute(
        "SELECT 1 FROM restocks WHERE sku = ? AND received = 0 LIMIT 1",
        (by_sku(sku).sku,),
    ).fetchone()
    return row is not None


def due_restocks(conn: sqlite3.Connection, on: date) -> list[sqlite3.Row]:
    """Deliveries that have arrived by `on` and not yet been booked in."""
    return conn.execute(
        "SELECT * FROM restocks WHERE received = 0 AND expected_on <= ?"
        " ORDER BY expected_on, id",
        (on.isoformat(),),
    ).fetchall()


def place_order(
    conn: sqlite3.Connection, quote: Quote, *, on: date | None = None
) -> Order:
    """Turn an accepted quote into an order, reserving stock if it is all there."""
    day = on or date.today()
    order_id = _next_id(conn, "orders", "SO")

    wanted = [(l.sku, l.quantity, l.total_cents) for l in quote.lines]
    shortfalls = [
        Shortfall(sku, qty, inv.stock(conn, sku).available)
        for sku, qty, _ in wanted
        if inv.stock(conn, sku).available < qty
    ]

    status = AWAITING_STOCK if shortfalls else RESERVED
    if not shortfalls:
        for sku, qty, _ in wanted:
            inv.reserve(conn, sku, qty)

    conn.execute(
        "INSERT INTO orders (id, customer, status, total_cents, margin_cents, placed_on)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (order_id, quote.customer, status, quote.total_cents, quote.margin_cents,
         day.isoformat()),
    )
    conn.executemany(
        "INSERT INTO order_lines (order_id, sku, quantity, total_cents) VALUES (?, ?, ?, ?)",
        [(order_id, sku, qty, total) for sku, qty, total in wanted],
    )
    conn.commit()

    for short in shortfalls:
        # Don't re-order what is already in transit and big enough to cover the
        # gap -- a SKU short on consecutive days would otherwise raise a fresh
        # purchase order every time and drain cash into excess stock.
        covered = conn.execute(
            "SELECT 1 FROM restocks WHERE sku = ? AND received = 0 AND quantity >= ?"
            " LIMIT 1",
            (short.sku, short.short_by),
        ).fetchone()
        if covered:
            continue
        product = by_sku(short.sku)
        stock_row = inv.stock(conn, short.sku)
        raise_restock(
            conn,
            short.sku,
            max(short.short_by, stock_row.reorder_qty, product.min_order_qty),
            on=day,
        )

    return Order(
        id=order_id,
        customer=quote.customer,
        status=status,
        total_cents=quote.total_cents,
        margin_cents=quote.margin_cents,
        placed_on=day.isoformat(),
        lines=tuple(OrderLine(sku, qty, total) for sku, qty, total in wanted),
        shortfalls=tuple(shortfalls),
    )


def get_order(conn: sqlite3.Connection, order_id: str) -> Order:
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such order {order_id}")
    lines = conn.execute(
        "SELECT sku, quantity, total_cents FROM order_lines WHERE order_id = ?",
        (order_id,),
    ).fetchall()
    return Order(
        id=row["id"],
        customer=row["customer"],
        status=row["status"],
        total_cents=row["total_cents"],
        margin_cents=row["margin_cents"],
        placed_on=row["placed_on"],
        lines=tuple(OrderLine(l["sku"], l["quantity"], l["total_cents"]) for l in lines),
    )


def fulfil(conn: sqlite3.Connection, order_id: str, *, on: date | None = None) -> Order:
    """Ship a reserved order and book the sale."""
    order = get_order(conn, order_id)
    if order.status != RESERVED:
        raise ValueError(f"{order_id} is {order.status}, not {RESERVED}")
    for line in order.lines:
        inv.consume(conn, line.sku, line.quantity)
    ledger.record(conn, ledger.SALE, order.total_cents, order_id, on=on)
    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (FULFILLED, order_id))
    conn.commit()
    return get_order(conn, order_id)


def cancel(conn: sqlite3.Connection, order_id: str) -> Order:
    """Release any held stock and close the order."""
    order = get_order(conn, order_id)
    if order.status in (FULFILLED, CANCELLED):
        raise ValueError(f"{order_id} is already {order.status}")
    if order.status == RESERVED:
        for line in order.lines:
            inv.release(conn, line.sku, line.quantity)
    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (CANCELLED, order_id))
    conn.commit()
    return get_order(conn, order_id)


def retry_awaiting(conn: sqlite3.Connection) -> list[str]:
    """After a delivery, promote any backorders that can now be covered."""
    promoted: list[str] = []
    rows = conn.execute(
        "SELECT id FROM orders WHERE status = ? ORDER BY placed_on, id", (AWAITING_STOCK,)
    ).fetchall()
    for row in rows:
        order = get_order(conn, row["id"])
        if all(inv.stock(conn, l.sku).available >= l.quantity for l in order.lines):
            for line in order.lines:
                inv.reserve(conn, line.sku, line.quantity)
            conn.execute("UPDATE orders SET status = ? WHERE id = ?", (RESERVED, order.id))
            promoted.append(order.id)
    conn.commit()
    return promoted
