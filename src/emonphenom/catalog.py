"""The Munder Difflin product catalog and supplier price book.

`unit_cost_cents` is what Munder Difflin pays its supplier for one unit.
`list_price_cents` is the undiscounted customer price for one unit. The spread
between them is the margin the quoting engine is allowed to spend on discounts,
down to the floor enforced in `pricing`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    category: str
    unit: str
    unit_cost_cents: int
    list_price_cents: int
    min_order_qty: int
    lead_time_days: int

    @property
    def margin_cents(self) -> int:
        """Gross margin per unit at list price."""
        return self.list_price_cents - self.unit_cost_cents

    def matches(self, term: str) -> bool:
        term = term.strip().lower()
        if not term:
            return False
        return (
            term in self.sku.lower()
            or term in self.name.lower()
            or term in self.category.lower()
        )


CATALOG: tuple[Product, ...] = (
    Product("A4-COPY-80", "A4 copy paper, 80gsm", "copy", "ream", 380, 649, 1, 3),
    Product("LTR-COPY-20", "Letter copy paper, 20lb", "copy", "ream", 355, 599, 1, 2),
    Product("LGL-COPY-20", "Legal copy paper, 20lb", "copy", "ream", 430, 749, 1, 4),
    Product("RECY-LTR-30", "Recycled letter, 30% PCW", "copy", "ream", 412, 699, 1, 5),
    Product("COLOR-ASST", "Assorted color paper", "specialty", "ream", 690, 1199, 1, 6),
    Product("CARD-110-WHT", "Cardstock, 110lb white", "cardstock", "pack", 940, 1649, 1, 5),
    Product("CARD-110-CRM", "Cardstock, 110lb cream", "cardstock", "pack", 985, 1699, 1, 7),
    Product("GLOSS-PHOTO", "Glossy photo paper, 8.5x11", "specialty", "pack", 1180, 2099, 1, 6),
    Product("ENV-10-WHT", "#10 business envelopes", "envelopes", "box", 620, 1099, 1, 3),
    Product("ENV-9-WHT", "#9 reply envelopes", "envelopes", "box", 585, 1049, 1, 4),
    Product("ENV-A7-CRM", "A7 invitation envelopes, cream", "envelopes", "box", 830, 1499, 1, 8),
    Product("LTRHD-CUSTOM", "Custom printed letterhead", "print", "ream", 1450, 2799, 2, 12),
    Product("NCR-3PT", "Carbonless 3-part forms", "forms", "box", 2240, 3899, 1, 10),
    Product("THERM-80", "Thermal receipt rolls, 80mm", "forms", "case", 1690, 2899, 1, 5),
    Product("POSTER-24", "Poster paper roll, 24in", "wide-format", "roll", 2050, 3599, 1, 9),
    Product("BANNER-36", "Banner stock roll, 36in", "wide-format", "roll", 3120, 5499, 1, 14),
)

_BY_SKU: dict[str, Product] = {p.sku: p for p in CATALOG}


class UnknownSKU(KeyError):
    """Raised when a SKU is not in the catalog."""


def by_sku(sku: str) -> Product:
    try:
        return _BY_SKU[sku.strip().upper()]
    except KeyError:
        raise UnknownSKU(f"{sku!r} is not a Munder Difflin SKU") from None


def search(term: str) -> list[Product]:
    """Catalog search, most specific match first (SKU prefix, then name)."""
    term_l = term.strip().lower()
    hits = [p for p in CATALOG if p.matches(term_l)]
    hits.sort(key=lambda p: (not p.sku.lower().startswith(term_l), p.name))
    return hits


def categories() -> list[str]:
    return sorted({p.category for p in CATALOG})
