"""A Munder Difflin trading month.

Single transactions never test an inventory policy. Reorder points, lead times
and the margin floor only prove themselves over time, against demand that does
not politely match what is on the shelf. This module runs the business day by
day so those rules show up as money.

The simulation is deterministic: the same seed produces the same month, so a
change in policy can be compared against a baseline rather than against noise.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from emonphenom import fulfillment as ful
from emonphenom import inventory as inv
from emonphenom import ledger
from emonphenom.catalog import by_sku
from emonphenom.customers import CUSTOMERS, CustomerProfile
from emonphenom.money import fmt
from emonphenom.pricing import build_quote


__all__ = [
    "CUSTOMERS", "CustomerProfile", "DaySnapshot", "SimulationResult", "simulate",
]


@dataclass(frozen=True)
class DaySnapshot:
    day: int
    on: date
    orders_taken: int
    orders_backordered: int
    orders_shipped: int
    revenue_cents: int
    cash_cents: int
    stock_at_cost_cents: int
    purchase_orders_raised: int
    restocks_deferred: int
    deliveries_received: int
    skus_below_reorder: int
    open_backorders: int


@dataclass
class SimulationResult:
    days: int
    seed: int
    start: date
    snapshots: list[DaySnapshot] = field(default_factory=list)
    orders_taken: int = 0
    orders_backordered: int = 0
    orders_shipped: int = 0
    purchase_orders: int = 0
    restocks_deferred: int = 0
    deliveries: int = 0
    margin_floor_hits: int = 0
    lost_line_attempts: int = 0

    @property
    def end(self) -> date:
        return self.start + timedelta(days=self.days - 1)

    @property
    def fill_rate(self) -> float:
        """Share of orders that could be reserved from stock on the spot."""
        if not self.orders_taken:
            return 0.0
        return (self.orders_taken - self.orders_backordered) / self.orders_taken * 100

    @property
    def revenue_cents(self) -> int:
        return self.snapshots[-1].revenue_cents if self.snapshots else 0

    @property
    def closing_cash_cents(self) -> int:
        return self.snapshots[-1].cash_cents if self.snapshots else 0

    @property
    def low_water_cash_cents(self) -> int:
        return min((s.cash_cents for s in self.snapshots), default=0)

    @property
    def days_cash_constrained(self) -> int:
        """Days the business wanted stock it could not pay for."""
        return sum(1 for s in self.snapshots if s.restocks_deferred)

    @property
    def unshipped_at_close(self) -> int:
        return self.snapshots[-1].open_backorders if self.snapshots else 0

    def table(self) -> str:
        head = (
            f"{'day':>3} {'date':<11}{'taken':>6}{'b/o':>5}{'ship':>5}"
            f"{'cash':>13}{'stock':>13}{'PO':>4}{'recv':>5}{'low':>5}"
        )
        rows = [head, "-" * len(head)]
        for s in self.snapshots:
            rows.append(
                f"{s.day:>3} {s.on.isoformat():<11}{s.orders_taken:>6}"
                f"{s.orders_backordered:>5}{s.orders_shipped:>5}"
                f"{fmt(s.cash_cents):>13}{fmt(s.stock_at_cost_cents):>13}"
                f"{s.purchase_orders_raised:>4}{s.deliveries_received:>5}"
                f"{s.skus_below_reorder:>5}"
            )
        return "\n".join(rows)

    def summary(self) -> str:
        lines = [
            f"{self.days} trading days, {self.start.isoformat()} to {self.end.isoformat()}"
            f"  (seed {self.seed})",
            "",
            f"{'orders taken':<26}{self.orders_taken:>14,}",
            f"{'  shipped':<26}{self.orders_shipped:>14,}",
            f"{'  backordered on arrival':<26}{self.orders_backordered:>14,}",
            f"{'  still unshipped at close':<26}{self.unshipped_at_close:>14,}",
            f"{'fill rate':<26}{self.fill_rate:>13.1f}%",
            "",
            f"{'revenue':<26}{fmt(self.revenue_cents):>14}",
            f"{'closing cash':<26}{fmt(self.closing_cash_cents):>14}",
            f"{'lowest cash in month':<26}{fmt(self.low_water_cash_cents):>14}",
            "",
            f"{'purchase orders raised':<26}{self.purchase_orders:>14,}",
            f"{'restocks deferred (no cash)':<26}{self.restocks_deferred:>14,}",
            f"{'days cash-constrained':<26}{self.days_cash_constrained:>14,}",
            f"{'deliveries received':<26}{self.deliveries:>14,}",
            f"{'lines held at margin floor':<26}{self.margin_floor_hits:>14,}",
        ]
        return "\n".join(lines)

    def to_csv(self) -> str:
        header = ("day,date,orders_taken,orders_backordered,orders_shipped,"
                  "revenue_cents,cash_cents,stock_at_cost_cents,"
                  "purchase_orders_raised,deliveries_received,"
                  "skus_below_reorder,open_backorders")
        rows = [header]
        for s in self.snapshots:
            rows.append(
                f"{s.day},{s.on.isoformat()},{s.orders_taken},{s.orders_backordered},"
                f"{s.orders_shipped},{s.revenue_cents},{s.cash_cents},"
                f"{s.stock_at_cost_cents},{s.purchase_orders_raised},"
                f"{s.deliveries_received},{s.skus_below_reorder},{s.open_backorders}"
            )
        return "\n".join(rows) + "\n"


def _basket(rng: random.Random, profile: CustomerProfile) -> list[tuple[str, int]]:
    """One customer's shopping list for the day."""
    count = rng.randint(1, max(1, min(profile.max_lines, len(profile.skus))))
    chosen = rng.sample(profile.skus, count)
    basket: list[tuple[str, int]] = []
    for sku in chosen:
        quantity = rng.randint(profile.min_qty, profile.max_qty)
        # never ask for less than the catalog will sell
        basket.append((sku, max(quantity, by_sku(sku).min_order_qty)))
    return basket


def _replenish(
    conn: sqlite3.Connection,
    on: date,
    *,
    cash_floor_cents: int,
    customers: tuple[CustomerProfile, ...],
) -> tuple[int, int]:
    """Standing policy, within what the bank account allows.

    Returns (raised, deferred). Deferred restocks are the ones the business
    needed but could not afford today -- the number to watch when a service
    level looks unreachable.
    """
    from emonphenom import policy

    budget = ledger.available_cents(conn, cash_floor_cents)
    decided = policy.plan(policy.candidates(conn, customers), budget)
    for candidate in decided.funded:
        ful.raise_restock(conn, candidate.sku, candidate.quantity, on=on)
    return len(decided.funded), len(decided.deferred)


def simulate(
    conn: sqlite3.Connection,
    days: int = 30,
    *,
    seed: int = 1,
    start: date | None = None,
    customers: tuple[CustomerProfile, ...] = CUSTOMERS,
    cash_floor_cents: int = 500_000,
) -> SimulationResult:
    """Run `days` trading days against a seeded database.

    `cash_floor_cents` is the operating balance replenishment may not spend
    below. Purchase orders are committed when raised but paid on delivery, so
    the budget nets off everything already in transit.
    """
    if days <= 0:
        raise ValueError("days must be positive")

    rng = random.Random(seed)
    first = start or date.today()
    result = SimulationResult(days=days, seed=seed, start=first)

    for offset in range(days):
        today = first + timedelta(days=offset)

        # 1. Goods in: anything the supplier promised by today.
        due = ful.due_restocks(conn, today)
        for row in due:
            ful.receive_restock(conn, row["id"], on=today)
        result.deliveries += len(due)

        # 2. Backorders that the delivery just unblocked.
        ful.retry_awaiting(conn)

        # 3. Goods out: yesterday's reserved orders are picked and shipped today.
        shipped = 0
        pending = conn.execute(
            "SELECT id FROM orders WHERE status = ? AND placed_on < ? ORDER BY placed_on, id",
            (ful.RESERVED, today.isoformat()),
        ).fetchall()
        for row in pending:
            ful.fulfil(conn, row["id"], on=today)
            shipped += 1

        # 4. Demand.
        taken = backordered = 0
        for profile in customers:
            if rng.random() >= profile.order_probability:
                continue
            discount = rng.randint(0, profile.max_rep_discount_pct)
            rush = rng.random() < profile.rush_probability
            try:
                quote = build_quote(
                    f"Q-SIM-{offset:03d}-{taken:02d}",
                    profile.name,
                    _basket(rng, profile),
                    rush=rush,
                    rep_discount_pct=discount,
                    issued_on=today,
                )
            except ValueError:
                result.lost_line_attempts += 1
                continue

            result.margin_floor_hits += sum(
                1 for line in quote.lines if line.margin_floor_applied
            )
            # Every purchasing decision goes through the budgeted step below.
            order = ful.place_order(conn, quote, on=today, auto_restock=False)
            taken += 1
            if order.shortfalls:
                backordered += 1

        # 5. Standing replenishment policy, inside the cash constraint.
        raised, deferred = _replenish(
            conn, today, cash_floor_cents=cash_floor_cents, customers=customers
        )

        result.orders_taken += taken
        result.orders_backordered += backordered
        result.orders_shipped += shipped
        result.purchase_orders += raised
        result.restocks_deferred += deferred

        report = ledger.report(conn)
        result.snapshots.append(
            DaySnapshot(
                day=offset + 1,
                on=today,
                orders_taken=taken,
                orders_backordered=backordered,
                orders_shipped=shipped,
                revenue_cents=report.revenue_cents,
                cash_cents=report.cash_cents,
                stock_at_cost_cents=report.stock_at_cost_cents,
                purchase_orders_raised=raised,
                restocks_deferred=deferred,
                deliveries_received=len(due),
                skus_below_reorder=len(inv.below_reorder(conn)),
                open_backorders=report.orders_awaiting_stock,
            )
        )

    return result
