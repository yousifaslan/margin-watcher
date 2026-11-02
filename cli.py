"""
Margin Watcher - command-line interface

Examples (see README.md for a full "add" example):
    python cli.py add --id attic --url SUPPLIER_PAGE_URL --sell 4500
    python cli.py list
    python cli.py check
    python cli.py watch --every 60
    python cli.py history --last 10
    python cli.py remove --id attic
"""

import argparse
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

import margin_watcher as mw


def checked(parse_function):
    """Let argparse use our own input checks and their messages."""
    def convert(text):
        try:
            return parse_function(text)
        except ValueError as error:
            raise argparse.ArgumentTypeError(str(error))
    return convert


def parse_minutes(text):
    """How often to check: whole minutes from 1 to 1440 (one day)."""
    if not str(text).isdigit() or int(text) < 1 or int(text) > 1440:
        raise ValueError("Minutes must be a whole number from 1 to 1440.")
    return int(text)


def parse_count(text):
    if not str(text).isdigit() or int(text) < 1 or int(text) > 1000:
        raise ValueError("Count must be a whole number from 1 to 1000.")
    return int(text)


def error(message):
    print(f"Error: {message}", file=sys.stderr)
    return 1


# ---------- One function per command ----------
def cmd_add(args):
    products = mw.load_products()
    if args.id in products:
        return error(f"'{args.id}' already exists. Remove it first.")
    products[args.id] = {"url": args.url, "sell_try": args.sell,
                         "min_margin": args.min_margin}
    mw.save_products(products)
    print(f"Added '{args.id}': you sell at {args.sell:.2f} TL, "
          f"alert below {args.min_margin}% margin.")
    print("Run 'python cli.py check' to read its supplier price now.")
    return 0


def cmd_remove(args):
    products = mw.load_products()
    if args.id not in products:
        return error(f"no product with ID '{args.id}'.")
    del products[args.id]
    mw.save_products(products)
    state = mw.load_state()
    state.pop(args.id, None)
    mw.save_json(mw.STATE_FILE, state)
    print(f"Removed '{args.id}'.")
    return 0


def cmd_list(args):
    products = mw.load_products()
    state = mw.load_state()
    if not products:
        print("No products yet. Add one with: python cli.py add --help")
        return 0
    print(f"{'ID':<12}{'Product':<24}{'Supplier':>11}{'Cost TL':>10}"
          f"{'Sell TL':>10}{'Margin':>8}  Status")
    print("-" * 86)
    for pid, product in products.items():
        sell = product["sell_try"]
        seen = state.get(pid)
        if not isinstance(seen, dict) or "margin" not in seen:
            print(f"{pid:<12}{'(not checked yet)':<24}{'':>21}{sell:>10.2f}")
            continue
        status = "OK"
        if seen["margin"] < product["min_margin"]:
            status = "LOW MARGIN"
        if seen.get("stock") == 0:
            status += ", OUT OF STOCK"
        supplier = f"{seen['price']:.2f} {seen['currency']}"
        print(f"{pid:<12}{seen['title'][:23]:<24}{supplier:>11}"
              f"{seen['cost_try']:>10.2f}{sell:>10.2f}"
              f"{seen['margin']:>7.1f}%  {status}")
    return 0


def cmd_check(args):
    failed = mw.run_check()
    if failed > 0:
        return 1
    return 0


def cmd_watch(args):
    scheduler = BlockingScheduler()
    scheduler.add_job(mw.run_check, "interval", minutes=args.every,
                      max_instances=1, coalesce=True,
                      next_run_time=datetime.now())
    print(f"Watching every {args.every} minute(s). Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nStopped.")
    return 0


def cmd_history(args):
    try:
        with open(mw.ALERTS_FILE, encoding="utf-8") as file:
            lines = file.read().splitlines()
    except FileNotFoundError:
        lines = []
    if not lines:
        print("No alerts yet.")
    for line in lines[-args.last:]:
        print(line)
    return 0


# ---------- Parser ----------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Watch supplier prices and exchange rates, and alert "
                    "when your profit margin drops.")
    commands = parser.add_subparsers(dest="command", required=True,
                                     metavar="COMMAND")

    p = commands.add_parser("add", help="start watching a product")
    p.add_argument("--id", required=True, type=checked(mw.parse_id),
                   help="short name, e.g. attic")
    p.add_argument("--url", required=True, type=checked(mw.parse_url),
                   help="supplier product page")
    p.add_argument("--sell", required=True, type=checked(mw.parse_money),
                   help="your selling price in TL")
    p.add_argument("--min-margin", type=checked(mw.parse_percent),
                   default=20, help="alert below this %% (default 20)")
    p.set_defaults(func=cmd_add)

    p = commands.add_parser("remove", help="stop watching a product")
    p.add_argument("--id", required=True, type=checked(mw.parse_id))
    p.set_defaults(func=cmd_remove)

    p = commands.add_parser("list", help="products with last cost and margin")
    p.set_defaults(func=cmd_list)

    p = commands.add_parser("check", help="check every product once, now")
    p.set_defaults(func=cmd_check)

    p = commands.add_parser("watch", help="check automatically on a schedule")
    p.add_argument("--every", type=checked(parse_minutes), default=60,
                   help="minutes between checks (default 60)")
    p.set_defaults(func=cmd_watch)

    p = commands.add_parser("history", help="show the latest alerts")
    p.add_argument("--last", type=checked(parse_count), default=10,
                   help="how many alerts (default 10)")
    p.set_defaults(func=cmd_history)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as problem:
        return error(problem)


if __name__ == "__main__":
    sys.exit(main())
