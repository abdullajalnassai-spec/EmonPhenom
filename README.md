# EmonPhenom

The **Munder Difflin Paper Company** operations layer: a deterministic domain
core for quoting, inventory, fulfillment and the ledger, with a multi-agent
routing layer on top and optional Claude-backed parsing of plain-English
customer enquiries.

```
$ munder ask "how much for 1200 reams of letter copy paper?" --customer "Scranton SD"
quoting: Q-0001 for Scranton SD: $6,109.80 across 1 line(s), margin 30.3%
```

## Why it is shaped this way

The business logic is ordinary Python over SQLite and has no dependency on a
model, a network, or an API key. The agent layer sits *on top* of that core
rather than inside it. That split is the whole design:

- every rule that decides money or stock is unit-testable offline, and is
- the agents are thin: they parse intent, call the core, and format a reply.

The language model is used for exactly one job — turning "I need 200 boxes of
#10 envelopes, rush" into a structured request. It never prices anything, never
decides whether stock exists, and never touches the ledger.

## Layout

| Module | Responsibility |
|---|---|
| `catalog.py` | 16 SKUs with supplier cost, list price, min order, lead time |
| `money.py` | Integer cents and half-up rounding — no floats touch money |
| `pricing.py` | Volume tiers, rep concessions, the margin floor, rush surcharge |
| `inventory.py` | On-hand vs reserved vs available, reorder signalling |
| `fulfillment.py` | Order lifecycle, all-or-nothing reservation, supplier restocks |
| `ledger.py` | Signed cash ledger and the financial report |
| `requests_nl.py` | Free text → `ParsedRequest` (Claude, or regex fallback) |
| `agents/` | Five specialists + an orchestrator that routes between them |
| `roster.py` | Loads the 279 specialist definitions in `.claude/agents/` |

## Business rules worth knowing

**Volume tiers.** 100+ → 5%, 500+ → 10%, 1000+ → 15%, 5000+ → 20%.

**The margin floor.** No line may be sold for less than 15% over supplier cost.
Tiers alone top out at 20% and never threaten it — but a rep concession
(`rep_discount_pct`) can, so the line is clamped at the floor and flagged. A
quote that hit the wall says so instead of quietly losing money:

```
quoting: Q-0003 for Big Account: $4,080.00 across 1 line(s), margin 13.0%
         -- margin floor held on LTR-COPY-20
```

**Reservation is all-or-nothing.** An order that cannot be fully covered holds
*no* stock at all. A part-reserved order is the worst case for a paper company:
stock is locked, the customer still cannot be served, and nobody is told.
Instead the order goes to `awaiting_stock`, purchase orders are raised for the
shortfall, and `retry_awaiting()` promotes it once the delivery lands.

**Cash is the sum of the ledger.** Money in is positive, money out negative, so
the balance and the transaction list cannot disagree. Supplier cash moves on
*receipt*, not when the purchase order is raised.

## Install

```bash
pip install -e ".[dev]"          # core + pytest
pip install -e ".[nl,dev]"       # also the Claude request parser
```

The core has no third-party dependencies.

## Use

```bash
munder demo                      # a scripted day, in memory, end to end
munder catalog                   # the price book
munder stock                     # stock levels, reorder flags
munder report                    # cash, revenue, margin
munder ask "we'll take 400 reams of letter copy paper" --customer "Scranton SD"
munder ask "ship SO-0001"
munder ask "..." --offline       # never call Claude, use the regex parser
```

As a library:

```python
from emonphenom.db import fresh
from emonphenom.agents import Orchestrator

orch = Orchestrator(fresh("munder.db"))
result = orch.ask("200 boxes of #10 envelopes, rush", customer="Vance Refrigeration")
print(result.summary, result.data["quote"].describe())
```

## The Claude layer

`requests_nl.parse()` uses `claude-opus-5` via structured outputs
(`client.messages.parse`) when the `nl` extra is installed *and* a credential is
configured, and the deterministic parser otherwise. If a Claude call fails the
request still resolves — it falls back and records why in `ParsedRequest.notes`
rather than degrading silently.

Credentials resolve the usual way: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
or an `ant auth login` profile.

## The specialist roster

`.claude/agents/` carries 279 specialist definitions across 18 divisions
(sales, finance, engineering, marketing, security, ...). Two consumers, one set
of files:

* **Claude Code** discovers them automatically, so anyone who clones this
  repository can delegate to any specialist by name.
* **`emonphenom.roster`** reads the same files, so Python code can list, search
  and recommend them.

```bash
munder roster                        # divisions and counts
munder roster --search pricing
munder roster --division sales
```

```python
from emonphenom import roster

roster.advisors_for("quote")         # who to consult about a quote
roster.get("sales-deal-strategist")  # one specialist
```

The roster is advisory. Nothing in it prices, reserves or ships anything — the
domain core remains the only thing that decides money or stock.

## Tests

```bash
python -m pytest        # 77 tests, no network, no API key
```

Coverage is on the rules that cost money if they break: tier boundaries, the
margin floor under every discount, double-reservation of the same stock,
all-or-nothing ordering, the restock→promote→ship path, and ledger arithmetic.
