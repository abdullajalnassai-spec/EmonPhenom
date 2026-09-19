from emonphenom import fulfillment as ful
from emonphenom import ledger
from emonphenom.db import OPENING_CASH_CENTS
from emonphenom.money import fmt, pct_of, with_markup
from emonphenom.pricing import build_quote


def test_opening_position(conn):
    rep = ledger.report(conn)
    assert rep.opening_cash_cents == OPENING_CASH_CENTS
    assert rep.cash_cents == OPENING_CASH_CENTS
    assert rep.revenue_cents == 0
    assert rep.orders_fulfilled == 0
    assert rep.stock_at_cost_cents > 0


def test_cash_balance_is_the_sum_of_transactions(conn):
    ledger.record(conn, ledger.ADJUSTMENT, -5_000, "stocktake writedown")
    ledger.record(conn, ledger.ADJUSTMENT, 1_250, "found a pallet")
    assert ledger.cash_balance(conn) == OPENING_CASH_CENTS - 5_000 + 1_250


def test_report_tracks_a_completed_sale(conn):
    quote = build_quote("Q-1", "Scranton SD", [("LTR-COPY-20", 800)])
    order = ful.place_order(conn, quote)
    ful.fulfil(conn, order.id)

    rep = ledger.report(conn)
    assert rep.revenue_cents == quote.total_cents
    assert rep.orders_fulfilled == 1
    assert rep.committed_margin_cents == quote.margin_cents
    assert 0 < rep.gross_margin_pct < 100
    assert "gross margin" in rep.describe()


def test_history_is_newest_first(conn):
    ledger.record(conn, ledger.ADJUSTMENT, 100, "first")
    ledger.record(conn, ledger.ADJUSTMENT, 200, "second")
    assert [t.reference for t in ledger.history(conn, limit=2)] == ["second", "first"]


def test_money_helpers_round_half_up():
    assert pct_of(355, 15) == 53      # 53.25 -> 53
    assert pct_of(350, 15) == 53      # 52.5  -> 53 (half up, not banker's)
    assert with_markup(1000, 15) == 1150
    assert fmt(-123456) == "-$1,234.56"
    assert fmt(5) == "$0.05"
