"""Command line front door: `munder ask "..."`, plus a scripted demo."""

from __future__ import annotations

import argparse
import sqlite3
import sys

from emonphenom import fulfillment, inventory, ledger, roster
from emonphenom.simulation import simulate
from emonphenom import policy as policy_mod
from emonphenom import agents_sync
from emonphenom.agents import Orchestrator
from emonphenom.catalog import CATALOG
from emonphenom.db import fresh
from emonphenom.money import fmt


def _open(path: str) -> sqlite3.Connection:
    return fresh(path)


def cmd_ask(args: argparse.Namespace) -> int:
    orch = Orchestrator(_open(args.db))
    result = orch.ask(
        " ".join(args.text), customer=args.customer, prefer_claude=not args.offline
    )
    print(result)
    quote = result.data.get("quote")
    if quote is not None:
        print()
        print(quote.describe())
    for line in result.data.get("listing", []):
        print(f"  {line}")
    return 0 if result.ok else 1


def cmd_catalog(args: argparse.Namespace) -> int:
    for p in CATALOG:
        print(f"{p.sku:<13} {p.name:<34} {fmt(p.list_price_cents):>10}/{p.unit:<6} "
              f"cost {fmt(p.unit_cost_cents):>9}  lead {p.lead_time_days}d")
    return 0


def cmd_stock(args: argparse.Namespace) -> int:
    conn = _open(args.db)
    for s in inventory.levels(conn):
        flag = "  <-- reorder" if s.below_reorder_point else ""
        print(f"{s.sku:<13} on hand {s.on_hand:>6,}  reserved {s.reserved:>6,}  "
              f"available {s.available:>6,}{flag}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    print(ledger.report(_open(args.db)).describe())
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """A scripted day at Munder Difflin, end to end."""
    conn = fresh(":memory:")
    orch = Orchestrator(conn)
    script = [
        ("Scranton School District", "How much for 1200 reams of letter copy paper?"),
        ("Scranton School District", "We'll take 1200 reams of letter copy paper"),
        ("Vance Refrigeration", "how much for 50 rolls of banner stock, rush?"),
        ("Vance Refrigeration", "we'll take 50 rolls of banner stock, rush"),
        ("walk-in", "do we have any A7 invitation envelopes in stock?"),
    ]
    for customer, text in script:
        print(f"\n> [{customer}] {text}")
        print(" ", orch.ask(text, customer=customer, prefer_claude=False))

    open_pos = fulfillment.open_restocks(conn)
    if open_pos:
        print(f"\n> receiving {len(open_pos)} supplier delivery(ies)")
        for po in open_pos:
            fulfillment.receive_restock(conn, po["id"])
            print(f"  {po['id']}: {po['quantity']:,} x {po['sku']} at {fmt(po['cost_cents'])}")
        promoted = fulfillment.retry_awaiting(conn)
        print(f"  backorders now reservable: {promoted or 'none'}")

    for row in conn.execute("SELECT id FROM orders WHERE status = 'reserved'").fetchall():
        order = fulfillment.fulfil(conn, row["id"])
        print(f"\n> shipped {order.id} ({fmt(order.total_cents)})")

    print("\n" + ledger.report(conn).describe())
    return 0


def cmd_roster(args: argparse.Namespace) -> int:
    if args.search:
        hits = roster.search(args.search)
    elif args.division:
        hits = roster.by_division(args.division)
    else:
        counts = roster.divisions()
        if not counts:
            print("no roster found under .claude/agents/")
            return 1
        for division, count in counts.items():
            print(f"{division:<20}{count:>4}")
        print(f"{'total':<20}{sum(counts.values()):>4}")
        return 0

    if not hits:
        print("no roster specialist matches that")
        return 1
    for agent in hits[: args.limit]:
        print(agent.brief())
    if len(hits) > args.limit:
        print(f"... and {len(hits) - args.limit} more")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    result = simulate(fresh(":memory:"), args.days, seed=args.seed,
                      cash_floor_cents=round(args.cash_floor * 100))
    if not args.quiet:
        print(result.table())
        print()
    print(result.summary())
    if args.csv:
        with open(args.csv, "w", encoding="utf-8") as fh:
            fh.write(result.to_csv())
        print(f"\nwrote {args.csv}")
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    seeds = tuple(range(1, args.seeds + 1))
    floor = round(args.cash_floor * 100)
    before = policy_mod.evaluate("seeded", days=args.days, seeds=seeds,
                                 cash_floor_cents=floor)
    after = policy_mod.evaluate(
        "demand-sized", days=args.days, seeds=seeds,
        policy=policy_mod.compute(
            safety_factor=args.safety,
            cover_days=args.cover,
            cover_largest_order=not args.no_spike_cover,
        ),
        cash_floor_cents=floor,
    )
    print(policy_mod.compare(before, after))
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    if args.action == "status":
        installed = agents_sync.status()
        if installed is None:
            print("no roster installed -- run: munder agents sync")
            return 1
        print(installed.describe())
        return 0

    try:
        report = agents_sync.sync(source=args.source, ref=args.ref)
    except agents_sync.SyncError as exc:
        print(f"could not sync the roster: {exc}")
        return 1
    print(report.describe())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="munder", description="Munder Difflin operations")
    parser.add_argument("--db", default="munder.db", help="SQLite file (default: munder.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ask = sub.add_parser("ask", help="route a plain-English enquiry to an agent")
    p_ask.add_argument("text", nargs="+")
    p_ask.add_argument("--customer", default="walk-in")
    p_ask.add_argument("--offline", action="store_true",
                       help="never call Claude; use the heuristic parser")
    p_ask.set_defaults(func=cmd_ask)

    sub.add_parser("catalog", help="list the product catalog").set_defaults(func=cmd_catalog)
    sub.add_parser("stock", help="show stock levels").set_defaults(func=cmd_stock)
    sub.add_parser("report", help="financial summary").set_defaults(func=cmd_report)
    sub.add_parser("demo", help="run a scripted day, in memory").set_defaults(func=cmd_demo)

    p_sim = sub.add_parser("simulate", help="run a trading month, day by day")
    p_sim.add_argument("--days", type=int, default=30)
    p_sim.add_argument("--seed", type=int, default=1,
                       help="same seed, same month (default: 1)")
    p_sim.add_argument("--cash-floor", type=float, default=5000.0,
                       help="operating balance replenishment may not spend below")
    p_sim.add_argument("--csv", help="also write the daily rows to this file")
    p_sim.add_argument("--quiet", action="store_true", help="summary only")
    p_sim.set_defaults(func=cmd_simulate)

    p_tune = sub.add_parser(
        "tune", help="compare the seeded reorder policy against a demand-sized one")
    p_tune.add_argument("--days", type=int, default=60)
    p_tune.add_argument("--seeds", type=int, default=8, help="run seeds 1..N")
    p_tune.add_argument("--safety", type=float, default=policy_mod.DEFAULT_SAFETY_FACTOR)
    p_tune.add_argument("--cover", type=int, default=policy_mod.DEFAULT_COVER_DAYS)
    p_tune.add_argument("--cash-floor", type=float, default=5000.0,
                        help="operating balance replenishment may not spend below")
    p_tune.add_argument("--no-spike-cover", action="store_true",
                        help="size on average demand only, ignoring large single orders")
    p_tune.set_defaults(func=cmd_tune)

    p_agents = sub.add_parser(
        "agents", help="install the specialist roster from the Agents repository")
    agents_sub = p_agents.add_subparsers(dest="action", required=True)
    p_sync = agents_sub.add_parser("sync", help="fetch and install the roster")
    p_sync.add_argument("--source", default=agents_sync.DEFAULT_SOURCE)
    p_sync.add_argument("--ref", help="branch, tag or commit (default: the default branch)")
    p_sync.set_defaults(func=cmd_agents, action="sync")
    p_status = agents_sub.add_parser("status", help="what is installed, and from where")
    p_status.set_defaults(func=cmd_agents, action="status")

    p_roster = sub.add_parser("roster", help="the specialist roster in .claude/agents/")
    p_roster.add_argument("--search", help="match name, division or description")
    p_roster.add_argument("--division", help="list one division")
    p_roster.add_argument("--limit", type=int, default=20)
    p_roster.set_defaults(func=cmd_roster)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
