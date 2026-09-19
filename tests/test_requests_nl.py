import pytest

from emonphenom.requests_nl import (
    CATALOG_LOOKUP,
    FINANCE,
    FULFIL,
    ORDER,
    QUOTE,
    STOCK,
    extract_items,
    match_product,
    parse,
    parse_heuristic,
)


@pytest.mark.parametrize(
    "text,intent",
    [
        ("How much for 500 reams of A4?", QUOTE),
        ("what would 200 boxes of envelopes cost", QUOTE),
        ("do you have any banner stock in stock?", STOCK),
        ("what's our inventory on cardstock", STOCK),
        ("we'll take 300 reams of letter paper", ORDER),
        ("ship SO-0004 please", FULFIL),
        ("how are we doing on cash", FINANCE),
        ("tell me about glossy photo paper", CATALOG_LOOKUP),
    ],
)
def test_intent_detection(text, intent):
    assert parse_heuristic(text).intent == intent


def test_matches_product_by_partial_name():
    assert match_product("A4 copy").sku == "A4-COPY-80"
    assert match_product("#10 envelopes").sku == "ENV-10-WHT"
    assert match_product("cream cardstock").sku == "CARD-110-CRM"
    assert match_product("completely unrelated words") is None


def test_extracts_multiple_items_with_quantities():
    items = extract_items("200 boxes of #10 envelopes and 50 packs of white cardstock")
    assert items == (("ENV-10-WHT", 200), ("CARD-110-WHT", 50))


def test_strips_thousands_separators():
    assert extract_items("1,500 reams of A4 copy paper") == (("A4-COPY-80", 1500),)


def test_detects_rush_language():
    assert parse_heuristic("50 rolls of banner stock, rush please").rush is True
    assert parse_heuristic("50 rolls of banner stock whenever").rush is False


def test_picks_up_an_order_number():
    assert parse_heuristic("please ship SO-0012").order_id == "SO-0012"


def test_a_quantity_without_a_cue_word_is_still_a_quote():
    parsed = parse_heuristic("1000 reams of legal paper")
    assert parsed.intent == QUOTE
    assert parsed.items == (("LGL-COPY-20", 1000),)


def test_parse_stays_offline_when_asked():
    parsed = parse("500 reams of A4", prefer_claude=False)
    assert parsed.source == "heuristic"
    assert parsed.items == (("A4-COPY-80", 500),)


def test_unparseable_text_yields_no_items():
    assert extract_items("hello, just saying hi") == ()
