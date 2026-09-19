"""The cash ledger and the numbers the finance agent reports on.

Amounts are signed: money in is positive, money out is negative. Cash balance
is therefore just the sum of the column, which makes it impossible for the
balance and the transaction list to disagree.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from emonphenom.money import fmt

SALE = "sale"
SUPPLIER_PURCHASE = "supplier_purchase"
OPENING_BALANCE = "opening_balance"
ADJUSTMENT = "adjustment"


@dataclass(frozen=True)
class Transaction:
    id: int
    occurred_on: str
    kind: str
    reference: str | None
    amount_cents: int


@dataclass(frozen=True)
class FinancialReport:
    opening_cash_cents: int
    revenue_cents: int
    purchases_cents: int
    cash_cents: int
    orders_fulfilled: int
    orders_awaiting_stock: int
    committed_margin_cents: int
    stock_at_cost_cents: int

    @property
    def gross_margin_pct(self) -> float:
        return (
            self.committed_margin_cents / self.revenue_cents * 100
            if self.revenue_cents
            else 0.0
        )

    def describe(self) -> str:
        rows = [
            ("opening cash", self.opening_cash_cents),
            ("revenue (fulfilled sales)", self.revenue_cents),
            ("supplier purchases", -self.purchases_cents),
            ("cash on hand", self.cash_cents),
            ("realised margin", self.committed_margin_cents),
            ("stock at cost", self.stock_at_cost_cents),
        ]
        out = [f"{label:<28}{fmt(value):>14}" for label, value in rows]
        out.append(f"{'gross margin':<28}{self.gross_margin_pct:>13.1f}%")
        out.append(
            f"{'orders':<28}{self.orders_fulfilled:>9} fulfilled, "
            f"{self.orders_awaiting_stock} awaiting stock"
        )
        return "\n".join(out)


def record(
    conn: sqlite3.Connection,
    kind: str,
    amount_cents: int,
    reference: str | None = None,
    *,
    on: date | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO transactions (occurred_on, kind, reference, amount_cents)"
        " VALUES (?, ?, ?, ?)",
        ((on or date.today()).isoformat(), kind, reference, amount_cents),
    )
    return int(cur.lastrowid)


def cash_balance(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) AS c FROM transactions").fetchone()
    return int(row["c"])


def committed_cents(conn: sqlite3.Connection) -> int:
    """Cash already promised to suppliers: purchase orders raised, not yet paid.

    Spending is decided when a purchase order is raised but the money leaves on
    delivery, so the bank balance alone overstates what is actually available.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_cents), 0) AS c FROM restocks WHERE received = 0"
    ).fetchone()
    return int(row["c"])


def available_cents(conn: sqlite3.Connection, floor_cents: int = 0) -> int:
    """What may still be committed today without breaching the operating floor."""
    return max(cash_balance(conn) - committed_cents(conn) - floor_cents, 0)


def history(conn: sqlite3.Connection, limit: int = 20) -> list[Transaction]:
    rows = conn.execute(
        "SELECT * FROM transactions ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        Transaction(r["id"], r["occurred_on"], r["kind"], r["reference"], r["amount_cents"])
        for r in rows
    ]


def _sum(conn: sqlite3.Connection, kind: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS c FROM transactions WHERE kind = ?",
        (kind,),
    ).fetchone()
    return int(row["c"])


def report(conn: sqlite3.Connection) -> FinancialReport:
    from emonphenom.catalog import by_sku

    fulfilled = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(margin_cents), 0) AS m"
        " FROM orders WHERE status = 'fulfilled'"
    ).fetchone()
    awaiting = conn.execute(
        "SELECT COUNT(*) AS n FROM orders WHERE status = 'awaiting_stock'"
    ).fetchone()

    stock_cost = 0
    for row in conn.execute("SELECT sku, on_hand FROM inventory").fetchall():
        stock_cost += by_sku(row["sku"]).unit_cost_cents * row["on_hand"]

    return FinancialReport(
        opening_cash_cents=_sum(conn, OPENING_BALANCE),
        revenue_cents=_sum(conn, SALE),
        purchases_cents=-_sum(conn, SUPPLIER_PURCHASE),
        cash_cents=cash_balance(conn),
        orders_fulfilled=int(fulfilled["n"]),
        orders_awaiting_stock=int(awaiting["n"]),
        committed_margin_cents=int(fulfilled["m"]),
        stock_at_cost_cents=stock_cost,
    )
