"""Who buys paper from Munder Difflin, and how they behave.

Kept apart from the simulation loop so that pricing policy, replenishment
policy and the loop itself can all read the same demand assumptions without
importing one another.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CustomerProfile:
    """How one account behaves: what they buy, how often, how hard they push."""

    name: str
    order_probability: float
    skus: tuple[str, ...]
    min_qty: int
    max_qty: int
    rush_probability: float = 0.0
    max_rep_discount_pct: int = 0
    max_lines: int = 1


# A believable Scranton customer base: one whale on commodity copy paper, a
# couple of steady mid-market accounts, and some small irregular buyers.
CUSTOMERS: tuple[CustomerProfile, ...] = (
    CustomerProfile(
        "Scranton School District", 0.34,
        ("LTR-COPY-20", "A4-COPY-80", "RECY-LTR-30"),
        400, 1600, rush_probability=0.05, max_rep_discount_pct=22, max_lines=2,
    ),
    CustomerProfile(
        "Lackawanna County Clerk", 0.26,
        ("NCR-3PT", "ENV-10-WHT", "ENV-9-WHT", "LGL-COPY-20"),
        40, 260, rush_probability=0.10, max_rep_discount_pct=6, max_lines=2,
    ),
    CustomerProfile(
        "Steamtown Retail Group", 0.30,
        ("THERM-80", "GLOSS-PHOTO", "COLOR-ASST"),
        20, 140, rush_probability=0.20, max_rep_discount_pct=8,
    ),
    CustomerProfile(
        "Vance Refrigeration", 0.14,
        ("BANNER-36", "POSTER-24", "CARD-110-WHT"),
        8, 60, rush_probability=0.35, max_rep_discount_pct=4,
    ),
    CustomerProfile(
        "Dunmore Dental Associates", 0.12,
        ("LTRHD-CUSTOM", "ENV-A7-CRM", "CARD-110-CRM"),
        10, 70, rush_probability=0.08, max_rep_discount_pct=3,
    ),
    CustomerProfile(
        "Poor Richard's Pub", 0.18,
        ("POSTER-24", "COLOR-ASST", "CARD-110-WHT"),
        5, 40, rush_probability=0.15, max_rep_discount_pct=2,
    ),
)
