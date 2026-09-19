import pytest

from emonphenom.catalog import by_sku
from emonphenom.pricing import (
    MIN_MARGIN_PCT,
    build_quote,
    floor_unit_price_cents,
    price_line,
    tier_discount_pct,
)


@pytest.mark.parametrize(
    "quantity,expected",
    [(1, 0), (99, 0), (100, 5), (499, 5), (500, 10), (999, 10),
     (1000, 15), (4999, 15), (5000, 20), (100_000, 20)],
)
def test_volume_tiers(quantity, expected):
    assert tier_discount_pct(quantity) == expected


def test_line_applies_tier_discount():
    line = price_line("LTR-COPY-20", 100)
    assert line.base_cents == 599 * 100
    assert line.discount_pct == 5
    assert line.discount_cents == 2995
    assert line.total_cents == 56_905


def test_margin_floor_clamps_a_deep_rep_discount():
    line = price_line("LTR-COPY-20", 1000, rep_discount_pct=20)
    assert line.margin_floor_applied is True
    # 35% off list would be $3.89/ream, under the $4.08 floor.
    assert line.effective_unit_price_cents == floor_unit_price_cents(by_sku("LTR-COPY-20"))
    assert line.total_cents == 408_000
    assert line.margin_cents == 53_000


def test_margin_floor_never_sells_below_minimum_margin():
    for pct in (0, 10, 25, 50, 90, 100):
        line = price_line("A4-COPY-80", 2000, rep_discount_pct=pct)
        cost = line.unit_cost_cents * line.quantity
        assert line.total_cents >= cost, "quoted below cost"
        assert line.margin_cents * 100 >= cost * MIN_MARGIN_PCT - 1000


def test_rush_surcharge_applies_after_discount():
    plain = price_line("A4-COPY-80", 500)
    rush = price_line("A4-COPY-80", 500, rush=True)
    assert rush.rush_fee_cents > 0
    assert rush.total_cents == plain.total_cents + rush.rush_fee_cents


def test_minimum_order_quantity_is_enforced():
    with pytest.raises(ValueError, match="minimum order"):
        price_line("LTRHD-CUSTOM", 1)


def test_rejects_nonsense_quantities():
    with pytest.raises(ValueError):
        price_line("A4-COPY-80", 0)
    with pytest.raises(ValueError):
        price_line("A4-COPY-80", 10, rep_discount_pct=101)


def test_quote_totals_are_the_sum_of_their_lines():
    quote = build_quote("Q-1", "Scranton SD", [("A4-COPY-80", 600), ("ENV-10-WHT", 120)])
    assert quote.total_cents == sum(line.total_cents for line in quote.lines)
    assert quote.discount_cents == sum(line.discount_cents for line in quote.lines)
    assert quote.margin_cents == sum(line.margin_cents for line in quote.lines)
    assert quote.expires_on > quote.issued_on
    assert "TOTAL" in quote.describe()


def test_empty_quote_is_rejected():
    with pytest.raises(ValueError):
        build_quote("Q-2", "nobody", [])
