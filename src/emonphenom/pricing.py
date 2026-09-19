"""The quoting maths: volume discounts, the margin floor, and rush surcharges.

The one rule that matters commercially: a discount may never push a line below
`MIN_MARGIN_PCT` over what the supplier charges. When a tier discount would
breach that floor the line is clamped and flagged, so a sales agent can see
that the customer hit the wall rather than silently selling at a loss.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from emonphenom.catalog import Product, by_sku
from emonphenom.money import fmt, pct_of

# (minimum quantity, discount percent) -- evaluated highest-first.
VOLUME_TIERS: tuple[tuple[int, int], ...] = (
    (5000, 20),
    (1000, 15),
    (500, 10),
    (100, 5),
)

MIN_MARGIN_PCT = 15
RUSH_SURCHARGE_PCT = 12
QUOTE_VALID_DAYS = 14


def tier_discount_pct(quantity: int) -> int:
    """The volume discount percent a quantity earns."""
    for min_qty, pct in VOLUME_TIERS:
        if quantity >= min_qty:
            return pct
    return 0


def floor_unit_price_cents(product: Product) -> int:
    """Lowest per-unit price that still clears the minimum margin."""
    return product.unit_cost_cents + pct_of(product.unit_cost_cents, MIN_MARGIN_PCT)


@dataclass(frozen=True)
class QuoteLine:
    sku: str
    name: str
    quantity: int
    unit_list_price_cents: int
    unit_cost_cents: int
    base_cents: int
    discount_pct: int
    discount_cents: int
    rush_fee_cents: int
    total_cents: int
    margin_cents: int
    margin_floor_applied: bool

    @property
    def effective_unit_price_cents(self) -> int:
        return self.total_cents // self.quantity if self.quantity else 0

    def describe(self) -> str:
        note = "  [margin floor]" if self.margin_floor_applied else ""
        return (
            f"{self.quantity:>6,} x {self.sku:<13} {self.name:<32} "
            f"{fmt(self.total_cents):>12}{note}"
        )


@dataclass(frozen=True)
class Quote:
    quote_id: str
    customer: str
    lines: tuple[QuoteLine, ...]
    issued_on: date
    expires_on: date
    rush: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def base_cents(self) -> int:
        return sum(l.base_cents for l in self.lines)

    @property
    def discount_cents(self) -> int:
        return sum(l.discount_cents for l in self.lines)

    @property
    def rush_fee_cents(self) -> int:
        return sum(l.rush_fee_cents for l in self.lines)

    @property
    def total_cents(self) -> int:
        return sum(l.total_cents for l in self.lines)

    @property
    def margin_cents(self) -> int:
        return sum(l.margin_cents for l in self.lines)

    @property
    def margin_pct(self) -> float:
        return (self.margin_cents / self.total_cents * 100) if self.total_cents else 0.0

    def describe(self) -> str:
        head = [f"Quote {self.quote_id} for {self.customer}",
                f"issued {self.issued_on.isoformat()}, valid to {self.expires_on.isoformat()}"]
        body = [l.describe() for l in self.lines]
        foot = [
            f"{'subtotal':>56} {fmt(self.base_cents):>12}",
            f"{'volume discount':>56} {fmt(-self.discount_cents):>12}",
        ]
        if self.rush_fee_cents:
            foot.append(f"{'rush surcharge':>56} {fmt(self.rush_fee_cents):>12}")
        foot.append(f"{'TOTAL':>56} {fmt(self.total_cents):>12}")
        foot.append(f"{'margin':>56} {fmt(self.margin_cents):>12} ({self.margin_pct:.1f}%)")
        return "\n".join(head + [""] + body + [""] + foot + list(self.notes))


def price_line(
    sku: str,
    quantity: int,
    *,
    rush: bool = False,
    rep_discount_pct: int = 0,
) -> QuoteLine:
    """Price a single catalog line.

    `rep_discount_pct` is a concession a sales rep negotiated on top of the
    volume tier. It is the reason the margin floor exists: tiers alone top out
    at 20% and never threaten it, but a rep leaning on a big account can.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    product = by_sku(sku)
    if quantity < product.min_order_qty:
        raise ValueError(
            f"{product.sku} has a minimum order of {product.min_order_qty} {product.unit}(s)"
        )

    if not 0 <= rep_discount_pct <= 100:
        raise ValueError("rep_discount_pct must be between 0 and 100")

    base = product.list_price_cents * quantity
    pct = min(tier_discount_pct(quantity) + rep_discount_pct, 100)
    discount = pct_of(base, pct)
    net = base - discount

    floor_total = floor_unit_price_cents(product) * quantity
    clamped = net < floor_total
    if clamped:
        net = floor_total
        discount = base - net

    rush_fee = pct_of(net, RUSH_SURCHARGE_PCT) if rush else 0
    total = net + rush_fee
    cost = product.unit_cost_cents * quantity

    return QuoteLine(
        sku=product.sku,
        name=product.name,
        quantity=quantity,
        unit_list_price_cents=product.list_price_cents,
        unit_cost_cents=product.unit_cost_cents,
        base_cents=base,
        discount_pct=pct,
        discount_cents=discount,
        rush_fee_cents=rush_fee,
        total_cents=total,
        margin_cents=total - cost,
        margin_floor_applied=clamped,
    )


def build_quote(
    quote_id: str,
    customer: str,
    items: list[tuple[str, int]],
    *,
    rush: bool = False,
    rep_discount_pct: int = 0,
    issued_on: date | None = None,
    notes: tuple[str, ...] = (),
) -> Quote:
    """Price a whole basket."""
    if not items:
        raise ValueError("a quote needs at least one line")
    issued = issued_on or date.today()
    lines = tuple(
        price_line(sku, qty, rush=rush, rep_discount_pct=rep_discount_pct)
        for sku, qty in items
    )
    return Quote(
        quote_id=quote_id,
        customer=customer,
        lines=lines,
        issued_on=issued,
        expires_on=issued + timedelta(days=QUOTE_VALID_DAYS),
        rush=rush,
        notes=notes,
    )
