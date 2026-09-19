import pytest

from datetime import date

from emonphenom import inventory as inv
from emonphenom import ledger
from emonphenom.db import OPENING_CASH_CENTS, fresh
from emonphenom.simulation import CUSTOMERS, CustomerProfile, simulate

START = date(2026, 9, 1)


def run(days=14, seed=3, **kw):
    return simulate(fresh(":memory:"), days, seed=seed, start=START, **kw)


def test_same_seed_produces_the_same_month():
    a, b = run(), run()
    assert [s.__dict__ for s in a.snapshots] == [s.__dict__ for s in b.snapshots]
    assert (a.orders_taken, a.revenue_cents) == (b.orders_taken, b.revenue_cents)


def test_different_seeds_diverge():
    assert run(seed=3).revenue_cents != run(seed=99).revenue_cents


def test_one_snapshot_per_day_with_consecutive_dates():
    result = run(days=10)
    assert len(result.snapshots) == 10
    assert result.snapshots[0].on == START
    assert result.end == date(2026, 9, 10)
    for earlier, later in zip(result.snapshots, result.snapshots[1:]):
        assert (later.on - earlier.on).days == 1
        assert later.day == earlier.day + 1


def test_days_must_be_positive():
    with pytest.raises(ValueError):
        run(days=0)


def test_no_demand_still_replenishes_thin_stock():
    """With no customers there is no revenue -- but the standing reorder policy
    still tops up any SKU that opens below its reorder point, so cash falls by
    exactly the supplier spend and nothing else."""
    conn = fresh(":memory:")
    result = simulate(conn, 14, seed=3, start=START, customers=())
    assert result.orders_taken == 0
    assert result.revenue_cents == 0

    report = ledger.report(conn)
    assert report.purchases_cents > 0
    assert result.snapshots[-1].cash_cents == OPENING_CASH_CENTS - report.purchases_cents


def test_stock_never_goes_negative_and_reservations_stay_covered():
    conn = fresh(":memory:")
    simulate(conn, 20, seed=5, start=START)
    for level in inv.levels(conn):
        assert level.on_hand >= 0
        assert level.reserved >= 0
        assert level.reserved <= level.on_hand, f"{level.sku} promised more than it holds"


def test_snapshot_cash_matches_the_ledger():
    conn = fresh(":memory:")
    result = simulate(conn, 15, seed=4, start=START)
    assert result.snapshots[-1].cash_cents == ledger.cash_balance(conn)
    assert result.closing_cash_cents == ledger.cash_balance(conn)


def test_revenue_only_ever_grows():
    result = run(days=20, seed=6)
    revenues = [s.revenue_cents for s in result.snapshots]
    assert revenues == sorted(revenues)


def test_counters_are_internally_consistent():
    result = run(days=20, seed=2)
    assert result.orders_taken == sum(s.orders_taken for s in result.snapshots)
    assert result.orders_shipped == sum(s.orders_shipped for s in result.snapshots)
    assert result.orders_backordered <= result.orders_taken
    assert 0 <= result.fill_rate <= 100


def test_a_sku_is_not_reordered_while_a_covering_delivery_is_in_transit():
    conn = fresh(":memory:")
    simulate(conn, 25, seed=1, start=START)
    open_pos = conn.execute(
        "SELECT sku, COUNT(*) AS n FROM restocks WHERE received = 0 GROUP BY sku"
    ).fetchall()
    for row in open_pos:
        assert row["n"] <= 2, f"{row['sku']} has {row['n']} overlapping purchase orders"


def test_a_hard_pushing_whale_reaches_the_margin_floor():
    whale = CustomerProfile(
        "Price Crusher Inc", 1.0, ("LTR-COPY-20",), 1000, 1200,
        max_rep_discount_pct=25,
    )
    result = simulate(fresh(":memory:"), 10, seed=1, start=START, customers=(whale,))
    assert result.margin_floor_hits > 0


def test_csv_export_has_a_row_per_day():
    result = run(days=7)
    lines = result.to_csv().strip().split("\n")
    assert lines[0].startswith("day,date,")
    assert len(lines) == 8  # header + 7 days


def test_summary_and_table_render():
    result = run(days=5)
    assert "fill rate" in result.summary()
    assert "margin floor" in result.summary()
    assert len(result.table().split("\n")) == 7  # header + rule + 5 days


def test_the_default_customer_base_is_sane():
    assert len(CUSTOMERS) >= 5
    for profile in CUSTOMERS:
        assert 0 < profile.order_probability <= 1
        assert profile.min_qty <= profile.max_qty
        assert profile.skus
