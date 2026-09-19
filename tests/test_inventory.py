import pytest

from emonphenom import inventory as inv


def test_available_is_on_hand_minus_reserved(conn):
    inv.reserve(conn, "A4-COPY-80", 200)
    level = inv.stock(conn, "A4-COPY-80")
    assert level.on_hand == 1200
    assert level.reserved == 200
    assert level.available == 1000


def test_reservation_cannot_oversell(conn):
    with pytest.raises(inv.InsufficientStock):
        inv.reserve(conn, "BANNER-36", 19)  # only 18 on hand


def test_two_reservations_cannot_claim_the_same_stock(conn):
    inv.reserve(conn, "POSTER-24", 30)  # 35 on hand
    with pytest.raises(inv.InsufficientStock):
        inv.reserve(conn, "POSTER-24", 10)


def test_release_returns_stock(conn):
    inv.reserve(conn, "A4-COPY-80", 300)
    inv.release(conn, "A4-COPY-80", 300)
    assert inv.stock(conn, "A4-COPY-80").available == 1200


def test_consume_removes_stock_and_its_reservation(conn):
    inv.reserve(conn, "ENV-10-WHT", 100)
    inv.consume(conn, "ENV-10-WHT", 100)
    level = inv.stock(conn, "ENV-10-WHT")
    assert level.on_hand == 700
    assert level.reserved == 0


def test_receive_adds_stock(conn):
    inv.receive(conn, "BANNER-36", 60)
    assert inv.stock(conn, "BANNER-36").on_hand == 78


def test_below_reorder_flags_thin_skus(conn):
    flagged = {s.sku for s in inv.below_reorder(conn)}
    assert "ENV-A7-CRM" in flagged      # 75 on hand, reorder point 80
    assert "LTR-COPY-20" not in flagged  # 2400 on hand, reorder point 800


def test_unknown_sku_is_rejected(conn):
    with pytest.raises(KeyError):
        inv.stock(conn, "NOT-A-SKU")
