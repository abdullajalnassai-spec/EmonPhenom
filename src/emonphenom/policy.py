"""Sizing the replenishment policy from demand instead of from habit.

The seeded reorder points in `db.py` were picked by eye. This module computes
them the way an inventory planner would:

    reorder point = expected demand over the lead time x a safety factor
    reorder qty   = enough cover to not be back at the point next week

Demand is not measured from history here -- it is derived directly from the
customer profiles driving the simulation, which is the honest thing to do when
the demand process is known exactly. Against real sales you would estimate the
same quantities from the order book.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from emonphenom import inventory as inv
from emonphenom.catalog import CATALOG, by_sku
from emonphenom.customers import CUSTOMERS, CustomerProfile

# Tuned by sweeping both knobs over 8 seeded 60-day months (see `munder tune`).
# Covering the largest single order is what buys service; piling on safety
# factor past this point buys inventory, not fill rate.
DEFAULT_SAFETY_FACTOR = 0.6
DEFAULT_COVER_DAYS = 7


@dataclass(frozen=True)
class PolicyLine:
    sku: str
    demand_per_day: float
    lead_time_days: int
    reorder_point: int
    reorder_qty: int


def demand_per_day(sku: str, customers: tuple[CustomerProfile, ...] = CUSTOMERS) -> float:
    """Expected units per day for a SKU under a set of customer profiles.

    A customer who orders with probability p picks `count` distinct SKUs from
    their list, where count is uniform on 1..min(max_lines, len(skus)); so the
    chance any one of their SKUs is picked on an ordering day is E[count] over
    the length of their list.
    """
    sku = by_sku(sku).sku
    total = 0.0
    for profile in customers:
        if sku not in profile.skus:
            continue
        span = min(profile.max_lines, len(profile.skus))
        expected_lines = (1 + span) / 2
        pick_chance = expected_lines / len(profile.skus)
        expected_qty = (profile.min_qty + profile.max_qty) / 2
        total += profile.order_probability * pick_chance * expected_qty
    return total


def largest_single_order(
    sku: str, customers: tuple[CustomerProfile, ...] = CUSTOMERS
) -> int:
    """The biggest quantity any one customer can ask for in one go."""
    sku = by_sku(sku).sku
    sizes = [c.max_qty for c in customers if sku in c.skus]
    return max(sizes) if sizes else 0


def compute(
    customers: tuple[CustomerProfile, ...] = CUSTOMERS,
    *,
    safety_factor: float = DEFAULT_SAFETY_FACTOR,
    cover_days: int = DEFAULT_COVER_DAYS,
    cover_largest_order: bool = True,
) -> dict[str, PolicyLine]:
    """A replenishment policy for the whole catalog.

    `cover_largest_order` adds the biggest single order a customer can place to
    the reorder point. Demand here is lumpy -- one account can ask for 1,600
    reams in a single line -- and a point sized for *average* demand over the
    lead time can never absorb that, however much safety factor is piled on.
    """
    if safety_factor <= 0 or cover_days <= 0:
        raise ValueError("safety_factor and cover_days must be positive")

    policy: dict[str, PolicyLine] = {}
    for product in CATALOG:
        rate = demand_per_day(product.sku, customers)
        # +1 day: the policy is only checked once a day, so the gap can be a
        # full day older than the lead time by the time the order is raised.
        point = math.ceil(rate * (product.lead_time_days + 1) * safety_factor)
        spike = largest_single_order(product.sku, customers) if cover_largest_order else 0
        point += spike
        # Reordering less than one big order just means backordering again.
        qty = max(math.ceil(rate * cover_days), spike)
        policy[product.sku] = PolicyLine(
            sku=product.sku,
            demand_per_day=rate,
            lead_time_days=product.lead_time_days,
            reorder_point=max(point, 0),
            reorder_qty=max(qty, product.min_order_qty, 1),
        )
    return policy


def apply(conn: sqlite3.Connection, policy: dict[str, PolicyLine]) -> None:
    """Write a policy onto a database. Stock on hand is not touched."""
    for line in policy.values():
        inv.set_policy(
            conn,
            line.sku,
            reorder_point=line.reorder_point,
            reorder_qty=line.reorder_qty,
        )


def current(conn: sqlite3.Connection) -> dict[str, PolicyLine]:
    """Read back whatever policy a database is running."""
    out: dict[str, PolicyLine] = {}
    for level in inv.levels(conn):
        out[level.sku] = PolicyLine(
            sku=level.sku,
            demand_per_day=demand_per_day(level.sku),
            lead_time_days=by_sku(level.sku).lead_time_days,
            reorder_point=level.reorder_point,
            reorder_qty=level.reorder_qty,
        )
    return out


# --------------------------------------------------------------------------
# Comparing one policy against another
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Outcome:
    """What a policy did, averaged over several seeded months."""

    label: str
    runs: int
    days: int
    fill_rate: float
    orders_taken: float
    orders_shipped: float
    unshipped: float
    revenue_cents: int
    closing_cash_cents: int
    low_cash_cents: int
    avg_stock_cents: int
    purchase_orders: float
    margin_floor_hits: float
    restocks_deferred: float = 0.0


def evaluate(
    label: str,
    *,
    days: int = 60,
    seeds: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8),
    policy: dict[str, PolicyLine] | None = None,
    customers: tuple[CustomerProfile, ...] = CUSTOMERS,
    cash_floor_cents: int = 500_000,
) -> Outcome:
    """Run the same months under one policy and average the results."""
    from emonphenom.db import fresh
    from emonphenom.simulation import simulate

    if not seeds:
        raise ValueError("need at least one seed")

    results = []
    for seed in seeds:
        conn = fresh(":memory:")
        if policy is not None:
            apply(conn, policy)
        results.append(
            simulate(conn, days, seed=seed, customers=customers,
                     cash_floor_cents=cash_floor_cents)
        )
        conn.close()

    n = len(results)
    return Outcome(
        label=label,
        runs=n,
        days=days,
        fill_rate=sum(r.fill_rate for r in results) / n,
        orders_taken=sum(r.orders_taken for r in results) / n,
        orders_shipped=sum(r.orders_shipped for r in results) / n,
        unshipped=sum(r.unshipped_at_close for r in results) / n,
        revenue_cents=round(sum(r.revenue_cents for r in results) / n),
        closing_cash_cents=round(sum(r.closing_cash_cents for r in results) / n),
        low_cash_cents=round(sum(r.low_water_cash_cents for r in results) / n),
        avg_stock_cents=round(
            sum(
                sum(s.stock_at_cost_cents for s in r.snapshots) / len(r.snapshots)
                for r in results
            ) / n
        ),
        purchase_orders=sum(r.purchase_orders for r in results) / n,
        margin_floor_hits=sum(r.margin_floor_hits for r in results) / n,
        restocks_deferred=sum(r.restocks_deferred for r in results) / n,
    )


def compare(before: Outcome, after: Outcome) -> str:
    """A before/after table. Deltas are after minus before."""
    from emonphenom.money import fmt

    def pct(value: float) -> str:
        return f"{value:.1f}%"

    def delta_pp(a: float, b: float) -> str:
        return f"{b - a:+.1f}pp"

    def delta_num(a: float, b: float, places: int = 1) -> str:
        return f"{b - a:+.{places}f}"

    def delta_money(a: int, b: int) -> str:
        sign = "+" if b >= a else "-"
        return f"{sign}{fmt(abs(b - a)).lstrip('-')}"

    rows: list[tuple[str, str, str, str]] = [
        ("fill rate", pct(before.fill_rate), pct(after.fill_rate),
         delta_pp(before.fill_rate, after.fill_rate)),
        ("orders shipped", f"{before.orders_shipped:.1f}", f"{after.orders_shipped:.1f}",
         delta_num(before.orders_shipped, after.orders_shipped)),
        ("unshipped at close", f"{before.unshipped:.1f}", f"{after.unshipped:.1f}",
         delta_num(before.unshipped, after.unshipped)),
        ("revenue", fmt(before.revenue_cents), fmt(after.revenue_cents),
         delta_money(before.revenue_cents, after.revenue_cents)),
        ("closing cash", fmt(before.closing_cash_cents), fmt(after.closing_cash_cents),
         delta_money(before.closing_cash_cents, after.closing_cash_cents)),
        ("lowest cash", fmt(before.low_cash_cents), fmt(after.low_cash_cents),
         delta_money(before.low_cash_cents, after.low_cash_cents)),
        ("avg stock at cost", fmt(before.avg_stock_cents), fmt(after.avg_stock_cents),
         delta_money(before.avg_stock_cents, after.avg_stock_cents)),
        ("purchase orders", f"{before.purchase_orders:.1f}", f"{after.purchase_orders:.1f}",
         delta_num(before.purchase_orders, after.purchase_orders)),
        ("restocks deferred", f"{before.restocks_deferred:.1f}",
         f"{after.restocks_deferred:.1f}",
         delta_num(before.restocks_deferred, after.restocks_deferred)),
    ]

    head = f"{'':<22}{before.label:>16}{after.label:>16}{'change':>16}"
    out = [
        f"{before.runs} seeds x {before.days} days, identical demand under both policies",
        "",
        head,
        "-" * len(head),
    ]
    out += [f"{label:<22}{b:>16}{a:>16}{d:>16}" for label, b, a, d in rows]
    return "\n".join(out)


# --------------------------------------------------------------------------
# Replenishment under a cash constraint
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """A SKU that wants restocking, priced and scored."""

    sku: str
    quantity: int
    cost_cents: int
    margin_at_risk_cents: int  # margin per day exposed while this SKU is short

    @property
    def value_density(self) -> float:
        """Margin protected per cent spent -- the ranking when money is tight."""
        return self.margin_at_risk_cents / self.cost_cents if self.cost_cents else 0.0


@dataclass(frozen=True)
class Plan:
    funded: tuple[Candidate, ...]
    deferred: tuple[Candidate, ...]

    @property
    def cost_cents(self) -> int:
        return sum(c.cost_cents for c in self.funded)


def backordered_demand(conn: sqlite3.Connection) -> dict[str, int]:
    """Units per SKU promised to orders that are waiting for stock."""
    rows = conn.execute(
        "SELECT l.sku AS sku, SUM(l.quantity) AS qty FROM order_lines l"
        " JOIN orders o ON o.id = l.order_id"
        " WHERE o.status = 'awaiting_stock' GROUP BY l.sku"
    ).fetchall()
    return {r["sku"]: int(r["qty"]) for r in rows}


def candidates(
    conn: sqlite3.Connection, customers: tuple[CustomerProfile, ...] = CUSTOMERS
) -> list[Candidate]:
    """Everything that needs stock and has none on order.

    Two things qualify. A SKU at or below its reorder point is the standing
    policy. A SKU with customers already waiting on it is the urgent case --
    and it can happen well above the reorder point, because one large order can
    exceed the shelf without the shelf ever looking low. Leaving that case out
    means a backorder can sit unserved forever.
    """
    from emonphenom import fulfillment as ful

    wanted: dict[str, int] = {}
    for level in inv.below_reorder(conn):
        wanted[level.sku] = level.reorder_qty

    for sku, promised in backordered_demand(conn).items():
        shortfall = promised - inv.stock(conn, sku).available
        if shortfall > 0:
            level = inv.stock(conn, sku)
            wanted[sku] = max(wanted.get(sku, 0), shortfall, level.reorder_qty)

    out: list[Candidate] = []
    for sku, quantity in wanted.items():
        if ful.has_open_restock(conn, sku):
            continue
        product = by_sku(sku)
        quantity = max(quantity, product.min_order_qty)
        rate = demand_per_day(sku, customers)
        out.append(
            Candidate(
                sku=sku,
                quantity=quantity,
                cost_cents=product.unit_cost_cents * quantity,
                margin_at_risk_cents=round(rate * product.margin_cents),
            )
        )
    return sorted(out, key=lambda c: c.sku)


def plan(candidates_: list[Candidate], budget_cents: int) -> Plan:
    """Spend a budget on the restocks that protect the most margin per cent.

    Greedy by value density, and it keeps going past an item it cannot afford:
    a cheap fast-moving SKU should not be starved because an expensive one
    happened to rank above it.
    """
    if budget_cents < 0:
        raise ValueError("budget cannot be negative")

    ranked = sorted(
        candidates_, key=lambda c: (-c.value_density, -c.margin_at_risk_cents, c.sku)
    )
    funded: list[Candidate] = []
    deferred: list[Candidate] = []
    remaining = budget_cents
    for candidate in ranked:
        if candidate.cost_cents <= remaining:
            funded.append(candidate)
            remaining -= candidate.cost_cents
        else:
            deferred.append(candidate)
    return Plan(tuple(funded), tuple(deferred))
