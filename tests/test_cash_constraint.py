import pytest

from emonphenom import fulfillment as ful
from emonphenom import inventory as inv
from emonphenom import ledger, policy
from emonphenom.db import OPENING_CASH_CENTS, fresh
from emonphenom.pricing import build_quote
from emonphenom.simulation import simulate

from datetime import date

START = date(2026, 9, 1)


def test_an_open_purchase_order_is_committed_cash(conn):
    assert ledger.committed_cents(conn) == 0
    po = ful.raise_restock(conn, "BANNER-36", 60)
    cost = 3120 * 60

    # raised, not yet paid: the bank balance has not moved but the money is spoken for
    assert ledger.cash_balance(conn) == OPENING_CASH_CENTS
    assert ledger.committed_cents(conn) == cost
    assert ledger.available_cents(conn) == OPENING_CASH_CENTS - cost

    ful.receive_restock(conn, po)
    assert ledger.cash_balance(conn) == OPENING_CASH_CENTS - cost
    assert ledger.committed_cents(conn) == 0


def test_available_cash_respects_the_operating_floor(conn):
    floor = 2_000_00
    assert ledger.available_cents(conn, floor) == OPENING_CASH_CENTS - floor


def test_available_cash_never_reports_negative(conn):
    assert ledger.available_cents(conn, OPENING_CASH_CENTS * 10) == 0


def test_plan_spends_the_budget_on_the_densest_value_first():
    cheap_fast = policy.Candidate("A", 10, 1_000, 500)    # density 0.5
    dear_slow = policy.Candidate("B", 10, 10_000, 1_000)  # density 0.1
    decided = policy.plan([dear_slow, cheap_fast], 1_500)
    assert [c.sku for c in decided.funded] == ["A"]
    assert [c.sku for c in decided.deferred] == ["B"]
    assert decided.cost_cents == 1_000


def test_plan_keeps_going_past_something_it_cannot_afford():
    unaffordable = policy.Candidate("BIG", 1, 900_000, 900_000)  # density 1.0
    affordable = policy.Candidate("SMALL", 1, 100, 50)           # density 0.5
    decided = policy.plan([unaffordable, affordable], 1_000)
    assert [c.sku for c in decided.funded] == ["SMALL"]
    assert [c.sku for c in decided.deferred] == ["BIG"]


def test_plan_with_no_budget_defers_everything(conn):
    decided = policy.plan(policy.candidates(conn), 0)
    assert decided.funded == ()
    assert decided.cost_cents == 0


def test_plan_rejects_a_negative_budget():
    with pytest.raises(ValueError):
        policy.plan([], -1)


def test_a_backorder_above_the_reorder_point_still_gets_restocked(conn):
    """One big order can empty the shelf without the shelf ever looking low."""
    inv.set_policy(conn, "BANNER-36", reorder_point=0, reorder_qty=20)
    assert inv.below_reorder(conn) == [] or all(
        s.sku != "BANNER-36" for s in inv.below_reorder(conn)
    )

    quote = build_quote("Q-1", "Vance", [("BANNER-36", 50)])
    order = ful.place_order(conn, quote, auto_restock=False)
    assert order.status == ful.AWAITING_STOCK

    skus = [c.sku for c in policy.candidates(conn)]
    assert "BANNER-36" in skus
    candidate = next(c for c in policy.candidates(conn) if c.sku == "BANNER-36")
    assert candidate.quantity >= 32  # enough to clear the 50 against 18 on hand


def test_a_sku_already_on_order_is_not_a_candidate(conn):
    inv.set_policy(conn, "BANNER-36", reorder_point=100, reorder_qty=20)
    assert "BANNER-36" in [c.sku for c in policy.candidates(conn)]
    ful.raise_restock(conn, "BANNER-36", 20)
    assert "BANNER-36" not in [c.sku for c in policy.candidates(conn)]


def test_backordered_demand_is_summed_per_sku(conn):
    for _ in range(2):
        ful.place_order(
            conn, build_quote("Q", "Vance", [("BANNER-36", 50)]), auto_restock=False
        )
    assert policy.backordered_demand(conn)["BANNER-36"] == 100


@pytest.mark.parametrize("floor_cents", [0, 500_000, 1_500_000])
def test_cash_never_falls_below_the_operating_floor(floor_cents):
    conn = fresh(":memory:")
    policy.apply(conn, policy.compute())
    result = simulate(conn, 45, seed=2, start=START, cash_floor_cents=floor_cents)
    worst = min(s.cash_cents for s in result.snapshots)
    assert worst >= floor_cents, f"overdrew the floor by {floor_cents - worst}"
    assert ledger.cash_balance(conn) >= floor_cents


def test_a_punishing_floor_defers_restocks_instead_of_overdrawing():
    conn = fresh(":memory:")
    policy.apply(conn, policy.compute())
    result = simulate(conn, 30, seed=2, start=START, cash_floor_cents=2_400_000)
    assert result.restocks_deferred > 0
    assert result.days_cash_constrained > 0
    assert min(s.cash_cents for s in result.snapshots) >= 2_400_000


def test_deferrals_are_counted_per_day_and_in_total():
    conn = fresh(":memory:")
    policy.apply(conn, policy.compute())
    result = simulate(conn, 30, seed=5, start=START, cash_floor_cents=2_000_000)
    assert result.restocks_deferred == sum(s.restocks_deferred for s in result.snapshots)
    assert result.days_cash_constrained <= len(result.snapshots)
    assert "restocks deferred" in result.summary()
