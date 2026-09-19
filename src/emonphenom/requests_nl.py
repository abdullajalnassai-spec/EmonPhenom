"""Turning a customer's free text into a structured request.

Two paths, same output. `parse_heuristic` is pure Python and always available,
so the whole system -- and its test suite -- runs with no API key and no
network. `parse_with_claude` is the better parser and is used automatically
when the `nl` extra is installed and a credential is configured.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from emonphenom.catalog import CATALOG, Product

MODEL = "claude-opus-5"

QUOTE = "quote"
STOCK = "stock"
ORDER = "order"
FULFIL = "fulfil"
FINANCE = "finance"
CATALOG_LOOKUP = "catalog"

_INTENT_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (FINANCE, ("financial", "finances", "revenue", "cash", "margin report",
               "p&l", "profit", "how are we doing")),
    (FULFIL, ("ship", "fulfil", "fulfill", "dispatch", "send it out")),
    (ORDER, ("place an order", "place the order", "i'll take", "we'll take",
             "go ahead", "buy", "purchase order", "order")),
    (STOCK, ("in stock", "stock level", "inventory", "do you have",
             "how many", "on hand", "availability", "available")),
    (QUOTE, ("quote", "how much", "price", "pricing", "cost", "estimate")),
)

_UNIT_WORDS = r"\b(reams?|packs?|boxes|box|cases?|rolls?|sheets?|units?|of|the|a|an)\b"
_RUSH_CUES = ("rush", "urgent", "asap", "expedite", "overnight", "by tomorrow",
              "next day", "hurry")


@dataclass(frozen=True)
class ParsedRequest:
    intent: str
    customer: str = "walk-in"
    items: tuple[tuple[str, int], ...] = ()
    sku: str | None = None
    order_id: str | None = None
    rush: bool = False
    rep_discount_pct: int = 0
    raw_text: str = ""
    source: str = "heuristic"
    notes: tuple[str, ...] = field(default_factory=tuple)


def detect_intent(text: str) -> str:
    low = text.lower()
    for intent, cues in _INTENT_CUES:
        if any(cue in low for cue in cues):
            return intent
    return CATALOG_LOOKUP


def match_product(fragment: str) -> Product | None:
    """Score catalog entries by how many query tokens they contain."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", fragment.lower()) if len(t) > 1]
    if not tokens:
        return None
    best: tuple[int, str] | None = None
    winner: Product | None = None
    for product in CATALOG:
        haystack = f"{product.sku} {product.name} {product.category}".lower()
        score = sum(1 for t in tokens if t in haystack)
        if score == 0:
            continue
        key = (-score, product.sku)  # deterministic: best score, then SKU order
        if best is None or key < best:
            best, winner = key, product
    return winner


def extract_items(text: str) -> tuple[tuple[str, int], ...]:
    """Pull (sku, quantity) pairs out of free text."""
    items: list[tuple[str, int]] = []
    # Collapse thousands separators first: the chunk split below is comma-aware,
    # and "1,500 reams" must not become the two chunks "1" and "500 reams".
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    for chunk in re.split(r",|;|\band\b|\bplus\b", text, flags=re.I):
        qty_match = re.search(r"(\d[\d,]*)", chunk)
        if not qty_match:
            continue
        quantity = int(qty_match.group(1).replace(",", ""))
        if quantity <= 0:
            continue
        remainder = re.sub(_UNIT_WORDS, " ", chunk[qty_match.end():], flags=re.I)
        product = match_product(remainder)
        if product is not None:
            items.append((product.sku, quantity))
    return tuple(items)


def parse_heuristic(text: str, *, customer: str = "walk-in") -> ParsedRequest:
    intent = detect_intent(text)
    items = extract_items(text)
    order_match = re.search(r"\b(SO-\d{4})\b", text, re.I)
    sku_match = re.search(r"\b([A-Z]{3,6}-[A-Z0-9]{1,5}(?:-[A-Z0-9]{1,4})?)\b", text)

    # "how much for 500 reams" is a quote even though it names no cue word once
    # the quantity is found; a bare product name with no quantity is a lookup.
    if intent == CATALOG_LOOKUP and items:
        intent = QUOTE

    return ParsedRequest(
        intent=intent,
        customer=customer,
        items=items,
        sku=(sku_match.group(1).upper() if sku_match else
             (items[0][0] if items and intent == STOCK else None)),
        order_id=order_match.group(1).upper() if order_match else None,
        rush=any(cue in text.lower() for cue in _RUSH_CUES),
        raw_text=text,
        source="heuristic",
    )


def _claude_available() -> bool:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False
    try:
        import anthropic  # noqa: F401
        import pydantic  # noqa: F401
    except ImportError:
        return False
    return True


def parse_with_claude(text: str, *, customer: str = "walk-in") -> ParsedRequest:
    """Structured extraction of a customer enquiry.

    Raises ImportError/anthropic errors to the caller -- `parse` handles the
    fallback so that failures are visible rather than silently degraded.
    """
    import anthropic
    from pydantic import BaseModel, Field

    sku_lines = "\n".join(f"  {p.sku}: {p.name} (sold by the {p.unit})" for p in CATALOG)

    class Item(BaseModel):
        sku: str = Field(description="exact SKU from the catalog")
        quantity: int = Field(description="number of units, must be positive")

    class Enquiry(BaseModel):
        intent: str = Field(
            description="one of: quote, stock, order, fulfil, finance, catalog"
        )
        items: list[Item] = Field(default_factory=list)
        order_id: str | None = Field(default=None, description="e.g. SO-0007, if named")
        rush: bool = Field(default=False, description="true if the customer needs it expedited")

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=(
            "You route enquiries for Munder Difflin, a paper company. Map the "
            "customer's words onto the catalog below. Never invent a SKU; if "
            "nothing matches, return no items.\n\nCatalog:\n" + sku_lines
        ),
        messages=[{"role": "user", "content": text}],
        output_format=Enquiry,
    )
    parsed = response.parsed_output
    valid = {p.sku for p in CATALOG}
    items = tuple(
        (i.sku.upper(), i.quantity)
        for i in parsed.items
        if i.sku.upper() in valid and i.quantity > 0
    )
    return ParsedRequest(
        intent=parsed.intent if parsed.intent in
        {QUOTE, STOCK, ORDER, FULFIL, FINANCE, CATALOG_LOOKUP} else CATALOG_LOOKUP,
        customer=customer,
        items=items,
        sku=items[0][0] if items else None,
        order_id=parsed.order_id.upper() if parsed.order_id else None,
        rush=parsed.rush,
        raw_text=text,
        source="claude",
    )


def parse(text: str, *, customer: str = "walk-in", prefer_claude: bool = True) -> ParsedRequest:
    """Parse an enquiry, using Claude when it is configured and reachable."""
    if prefer_claude and _claude_available():
        try:
            return parse_with_claude(text, customer=customer)
        except Exception as exc:  # network, auth, schema -- fall back, but say so
            fallback = parse_heuristic(text, customer=customer)
            return ParsedRequest(
                **{**fallback.__dict__,
                   "notes": (f"claude parse failed ({type(exc).__name__}), used heuristic",)}
            )
    return parse_heuristic(text, customer=customer)
