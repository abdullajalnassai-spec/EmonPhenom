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
| `simulation.py` | Seeded day-by-day trading month with customer demand |
| `policy.py` | Demand-sized reorder points, budgeted replenishment, A/B evaluation |
| `customers.py` | The demand profiles pricing and replenishment both read |

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

## Simulation

A single transaction never tests an inventory policy. Reorder points, lead
times and the margin floor only prove themselves against demand that does not
politely match what is on the shelf.

```bash
munder simulate                      # 30 days, seeded, day by day
munder simulate --days 90 --seed 4
munder simulate --quiet              # summary only
munder simulate --csv month.csv      # daily rows for a spreadsheet
```

Six customer profiles drive demand -- a school district buying commodity copy
paper by the pallet and pushing hard on price, a county clerk on forms and
envelopes, a retail group on thermal rolls, and three small irregular buyers.
Each simulated day, in order: receive due deliveries, promote backorders the
delivery unblocked, ship yesterday's reserved orders, take new orders, then run
the standing reorder policy.

Same seed, same month -- so a policy change can be measured against a baseline
instead of against noise.

**What the default run shows.** Fill rate lands around 60-70%: roughly a third
of orders arrive to find the shelf short. That is not a bug in the simulation,
it is the seeded inventory policy being wrong. `munder tune` measures how
wrong, and `policy.py` fixes it.

## Tuning the reorder policy

```bash
munder tune                                  # seeded vs demand-sized, 8 seeds x 60 days
munder tune --days 90 --seeds 12
munder tune --no-spike-cover                 # size on average demand only
munder tune --safety 1.6 --cover 14          # sweep the knobs yourself
```

```
                                seeded    demand-sized          change
----------------------------------------------------------------------
fill rate                        68.1%           88.2%         +20.1pp
revenue                    $197,889.11     $239,585.19     +$41,696.08
lowest cash                 $24,509.37      $15,866.92      -$8,642.45
avg stock at cost           $41,050.19      $53,579.49     +$12,529.30
```

Both policies face identical demand -- same seeds, same customers, same days --
so the difference is the policy and nothing else.

**The finding that matters: demand here is lumpy.** One account can ask for
1,600 reams in a single line. A reorder point sized for *average* demand over
the lead time can never absorb that, however much safety factor is piled on:

| policy | fill rate | lowest cash | avg stock |
|---|---|---|---|
| seeded baseline | 68.1% | $24,509 | $41,050 |
| average demand only (`--no-spike-cover`) | 70.5% | $22,334 | $42,173 |
| + covers the largest single order | 88.2% | $15,867 | $53,579 |
| + safety 1.6, cover 14d | 92.0% | **-$204** | $71,765 |

Covering the largest plausible single order is worth ~18 points of fill rate.
Everything after that buys inventory, not service — and the most aggressive
setting overdraws the bank account. The defaults in `policy.py` sit at the knee
of that curve.

## The cash constraint

Replenishment spends money the business may not have. Purchase orders are
**committed when raised and paid on delivery**, so the bank balance alone
overstates what is available:

```
available = cash - committed (open purchase orders) - operating floor
```

Every purchasing decision goes through one budgeted step. When the budget will
not cover everything that needs stock, the planner ranks candidates by
**margin protected per cent spent** and funds down the list — and keeps going
past something it cannot afford, so a cheap fast mover is not starved by an
expensive one ranked above it. What it cannot fund is counted as a *deferred
restock* rather than silently dropped.

Two things qualify as needing stock: a SKU at or below its reorder point (the
standing policy), and a SKU with customers already waiting on it. The second
case matters because one large order can empty the shelf without the shelf ever
looking low — leaving it out means a backorder can sit unserved forever.

```bash
munder simulate --cash-floor 5000
munder tune --cash-floor 20000        # see service collapse as money tightens
```

| operating floor | fill rate | lowest cash | deferred restocks | revenue |
|---|---|---|---|---|
| $0 | 88.7% | $17,937 | 6.0 | $244,982 |
| $5,000 (default) | 87.8% | $19,043 | 13.8 | $244,982 |
| $10,000 | 85.6% | $19,830 | 29.5 | $244,982 |
| $20,000 | 79.3% | $24,753 | 82.5 | $199,455 |
| $30,000 | 70.6% | $25,000 | 188.2 | $193,912 |

**What the constraint revealed.** Before it existed, the most aggressive
reorder policy reached a 92% fill rate — financed by an overdraft. With cash
enforced it reaches 87%, the same as the tuned policy, while deferring five
times as many restocks. Its extra service was never real; it was borrowed.

Cash is now an invariant, not a report: `cash - committed >= floor` holds at
every point, because a delivery only ever pays down money that was already set
aside when the order was raised.

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
python -m pytest        # 119 tests, no network, no API key
```

Coverage is on the rules that cost money if they break: tier boundaries, the
margin floor under every discount, double-reservation of the same stock,
all-or-nothing ordering, the restock→promote→ship path, and ledger arithmetic.
