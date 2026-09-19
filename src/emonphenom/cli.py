"""Command line front door: `munder ask "..."`, plus a scripted demo."""

from __future__ import annotations

import argparse
import sqlite3
import sys

from emonphenom import fulfillment, inventory, ledger
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
