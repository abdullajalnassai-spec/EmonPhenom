import pytest

from emonphenom import inventory as inv
from emonphenom import policy
from emonphenom.db import fresh
from emonphenom.simulation import CustomerProfile


ONE_BUYER = (
    CustomerProfile("Solo", 0.5, ("A4-COPY-80",), 100, 300, max_lines=1),
)


def test_demand_rate_from_a_single_profile():
    # 0.5 chance/day x 1 line from 1 SKU x mean qty 200 = 100 units/day
    assert policy.demand_per_day("A4-COPY-80", ONE_BUYER) == pytest.approx(100.0)


def test_demand_rate_is_zero_for_an_unstocked_sku():
    assert policy.demand_per_day("BANNER-36", ONE_BUYER) == 0.0


def test_largest_single_order():
    assert policy.largest_single_order("A4-COPY-80", ONE_BUYER) == 300
    assert policy.largest_single_order("BANNER-36", ONE_BUYER) == 0


def test_reorder_point_covers_lead_time_demand():
    line = policy.compute(
        ONE_BUYER, safety_factor=1.0, cover_days=7, cover_largest_order=False
    )["A4-COPY-80"]
    # 100/day over a 3-day lead time, plus the one-day policy check = 400
    assert line.reorder_point == 400
    assert line.reorder_qty == 700


def test_spike_cover_adds_the_largest_single_order():
    plain = policy.compute(ONE_BUYER, safety_factor=1.0, cover_days=7,
                           cover_largest_order=False)["A4-COPY-80"]
    spiked = policy.compute(ONE_BUYER, safety_factor=1.0, cover_days=7,
                            cover_largest_order=True)["A4-COPY-80"]
    assert spiked.reorder_point == plain.reorder_point + 300
    assert spiked.reorder_qty >= 300


def test_reorder_quantity_respects_minimum_order():
    line = policy.compute(ONE_BUYER)["LTRHD-CUSTOM"]  # nobody buys it, MOQ 2
    assert line.reorder_qty >= 2


def test_policy_covers_the_whole_catalog():
    computed = policy.compute()
    assert len(computed) == 16
    assert all(line.reorder_qty > 0 for line in computed.values())


def test_invalid_parameters_are_rejected():
    with pytest.raises(ValueError):
        policy.compute(safety_factor=0)
    with pytest.raises(ValueError):
        policy.compute(cover_days=0)


def test_apply_writes_the_policy_without_touching_stock():
    conn = fresh(":memory:")
    before = inv.stock(conn, "A4-COPY-80")
    policy.apply(conn, policy.compute())
    after = inv.stock(conn, "A4-COPY-80")

    assert after.on_hand == before.on_hand
    assert after.reorder_point != before.reorder_point
    assert policy.current(conn)["A4-COPY-80"].reorder_point == after.reorder_point


def test_set_policy_validates():
    conn = fresh(":memory:")
    with pytest.raises(ValueError):
        inv.set_policy(conn, "A4-COPY-80", reorder_qty=0)
    with pytest.raises(ValueError):
        inv.set_policy(conn, "A4-COPY-80", reorder_point=-1)


def test_evaluate_needs_a_seed():
    with pytest.raises(ValueError):
        policy.evaluate("empty", days=5, seeds=())


def test_the_tuned_policy_beats_the_seeded_one_on_service():
    seeds = (1, 2, 3)
    before = policy.evaluate("seeded", days=40, seeds=seeds)
    after = policy.evaluate("tuned", days=40, seeds=seeds, policy=policy.compute())
    assert after.fill_rate > before.fill_rate
    assert after.unshipped < before.unshipped
    # and it is paid for in working capital, not conjured
    assert after.avg_stock_cents > before.avg_stock_cents


def test_compare_renders_both_labels_and_a_delta():
    seeds = (1,)
    before = policy.evaluate("seeded", days=20, seeds=seeds)
    after = policy.evaluate("tuned", days=20, seeds=seeds, policy=policy.compute())
    table = policy.compare(before, after)
    assert "seeded" in table and "tuned" in table
    assert "fill rate" in table and "avg stock at cost" in table
