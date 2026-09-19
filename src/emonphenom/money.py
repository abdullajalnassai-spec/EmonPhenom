"""Money handling.

Every monetary value in this package is an integer number of cents. Floats are
never used for money -- 0.1 + 0.2 problems in a quoting engine turn into
invoices that do not reconcile.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def pct_of(amount_cents: int, pct: Decimal | int | str) -> int:
    """Return `pct` percent of `amount_cents`, rounded half-up to the cent."""
    result = (Decimal(amount_cents) * Decimal(pct)) / Decimal(100)
    return int(result.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def with_markup(cost_cents: int, markup_pct: Decimal | int | str) -> int:
    """Apply a markup to a cost, rounding half-up."""
    return cost_cents + pct_of(cost_cents, markup_pct)


def fmt(cents: int) -> str:
    """Format cents as a display string, e.g. -1234 -> '-$12.34'."""
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{part:02d}"
