"""SQLite schema and seeding for the Munder Difflin back office."""

from __future__ import annotations

import sqlite3
from datetime import date

from emonphenom.catalog import CATALOG

OPENING_CASH_CENTS = 2_500_000  # $25,000 of working capital

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    sku            TEXT PRIMARY KEY,
    on_hand        INTEGER NOT NULL CHECK (on_hand >= 0),
    reserved       INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    reorder_point  INTEGER NOT NULL,
    reorder_qty    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id           TEXT PRIMARY KEY,
    customer     TEXT NOT NULL,
    status       TEXT NOT NULL,
    total_cents  INTEGER NOT NULL,
    margin_cents INTEGER NOT NULL,
    placed_on    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_lines (
    order_id    TEXT NOT NULL REFERENCES orders(id),
    sku         TEXT NOT NULL,
    quantity    INTEGER NOT NULL,
    total_cents INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS restocks (
    id          TEXT PRIMARY KEY,
    sku         TEXT NOT NULL,
    quantity    INTEGER NOT NULL,
    cost_cents  INTEGER NOT NULL,
    ordered_on  TEXT NOT NULL,
    expected_on TEXT NOT NULL,
    received    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_on  TEXT NOT NULL,
    kind         TEXT NOT NULL,
    reference    TEXT,
    amount_cents INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_lines_order ON order_lines(order_id);
CREATE INDEX IF NOT EXISTS idx_restocks_sku ON restocks(sku, received);
"""

# Opening stock position, tuned so some SKUs are healthy and some are thin --
# a seed where everything is plentiful never exercises the restock path.
_SEED_STOCK: dict[str, tuple[int, int, int]] = {
    # sku: (on_hand, reorder_point, reorder_qty)
    "A4-COPY-80": (1200, 400, 1500),
    "LTR-COPY-20": (2400, 800, 2500),
    "LGL-COPY-20": (600, 200, 800),
    "RECY-LTR-30": (450, 200, 700),
    "COLOR-ASST": (180, 80, 300),
    "CARD-110-WHT": (320, 120, 500),
    "CARD-110-CRM": (140, 100, 400),
    "GLOSS-PHOTO": (95, 60, 250),
    "ENV-10-WHT": (800, 250, 1000),
    "ENV-9-WHT": (410, 150, 600),
    "ENV-A7-CRM": (75, 80, 300),
    "LTRHD-CUSTOM": (60, 40, 200),
    "NCR-3PT": (110, 50, 200),
    "THERM-80": (240, 90, 300),
    "POSTER-24": (35, 20, 80),
    "BANNER-36": (18, 15, 60),
}


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn: sqlite3.Connection, *, on: date | None = None) -> None:
    """Load opening stock and working capital. Safe to call once per database."""
    day = (on or date.today()).isoformat()
    rows = [
        (p.sku, *_SEED_STOCK.get(p.sku, (100, 50, 200)))
        for p in CATALOG
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO inventory (sku, on_hand, reorder_point, reorder_qty)"
        " VALUES (?, ?, ?, ?)",
        rows,
    )
    already = conn.execute(
        "SELECT 1 FROM transactions WHERE kind = 'opening_balance'"
    ).fetchone()
    if not already:
        conn.execute(
            "INSERT INTO transactions (occurred_on, kind, reference, amount_cents)"
            " VALUES (?, 'opening_balance', 'seed', ?)",
            (day, OPENING_CASH_CENTS),
        )
    conn.commit()


def fresh(path: str = ":memory:", *, on: date | None = None) -> sqlite3.Connection:
    """A connected, migrated, seeded database in one call."""
    conn = connect(path)
    init_db(conn)
    seed(conn, on=on)
    return conn
