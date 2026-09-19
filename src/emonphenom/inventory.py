"""Stock positions, reservations, and reorder signalling.

`on_hand` is physical stock. `reserved` is stock promised to an accepted order
but not yet shipped. Only `available` -- the difference -- can be sold, which
is what stops two orders from being promised the same ream.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from emonphenom.catalog import by_sku


@dataclass(frozen=True)
class Stock:
    sku: str
    on_hand: int
    reserved: int
    reorder_point: int
    reorder_qty: int

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved

    @property
    def below_reorder_point(self) -> bool:
        return self.available <= self.reorder_point


class InsufficientStock(RuntimeError):
    def __init__(self, sku: str, wanted: int, available: int):
        self.sku, self.wanted, self.available = sku, wanted, available
        super().__init__(f"{sku}: wanted {wanted}, {available} available")


def _row_to_stock(row: sqlite3.Row) -> Stock:
    return Stock(
        sku=row["sku"],
        on_hand=row["on_hand"],
        reserved=row["reserved"],
        reorder_point=row["reorder_point"],
        reorder_qty=row["reorder_qty"],
    )


def stock(conn: sqlite3.Connection, sku: str) -> Stock:
    sku = by_sku(sku).sku
    row = conn.execute("SELECT * FROM inventory WHERE sku = ?", (sku,)).fetchone()
    if row is None:
        raise KeyError(f"{sku} has no inventory record")
    return _row_to_stock(row)


def levels(conn: sqlite3.Connection) -> list[Stock]:
    rows = conn.execute("SELECT * FROM inventory ORDER BY sku").fetchall()
    return [_row_to_stock(r) for r in rows]


def below_reorder(conn: sqlite3.Connection) -> list[Stock]:
    return [s for s in levels(conn) if s.below_reorder_point]


def can_fulfil(conn: sqlite3.Connection, sku: str, quantity: int) -> bool:
    return stock(conn, sku).available >= quantity


def reserve(conn: sqlite3.Connection, sku: str, quantity: int) -> None:
    """Promise stock to an order. Raises InsufficientStock rather than oversell."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    current = stock(conn, sku)
    if current.available < quantity:
        raise InsufficientStock(current.sku, quantity, current.available)
    conn.execute(
        "UPDATE inventory SET reserved = reserved + ? WHERE sku = ?",
        (quantity, current.sku),
    )


def release(conn: sqlite3.Connection, sku: str, quantity: int) -> None:
    """Give a reservation back (order cancelled)."""
    current = stock(conn, sku)
    conn.execute(
        "UPDATE inventory SET reserved = MAX(reserved - ?, 0) WHERE sku = ?",
        (quantity, current.sku),
    )


def consume(conn: sqlite3.Connection, sku: str, quantity: int) -> None:
    """Ship reserved stock: it leaves the building."""
    current = stock(conn, sku)
    if current.on_hand < quantity:
        raise InsufficientStock(current.sku, quantity, current.on_hand)
    conn.execute(
        "UPDATE inventory SET on_hand = on_hand - ?, reserved = MAX(reserved - ?, 0)"
        " WHERE sku = ?",
        (quantity, quantity, current.sku),
    )


def receive(conn: sqlite3.Connection, sku: str, quantity: int) -> None:
    """Supplier delivery lands on the dock."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    current = stock(conn, sku)
    conn.execute(
        "UPDATE inventory SET on_hand = on_hand + ? WHERE sku = ?",
        (quantity, current.sku),
    )
