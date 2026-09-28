import argparse
import sys
from datetime import date

from ledger import db, importers, report
from ledger.models import CATEGORIES, Expense


def parse(argv):
    p = argparse.ArgumentParser(prog="ledger")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("amount", type=float)
    a.add_argument("category", choices=CATEGORIES)
    a.add_argument("note", nargs="?", default="")
    a.add_argument("--day", type=date.fromisoformat)
    for name in ("list", "summary", "report"):
        s = sub.add_parser(name)
        s.add_argument("--month")
        if name == "list":
            s.add_argument("--category", choices=CATEGORIES)
        if name == "report":
            s.add_argument("--html", required=True)
    i = sub.add_parser("import")
    i.add_argument("csv")
    return p.parse_args(argv)


def main(argv=None, out=sys.stdout):
    args = parse(argv if argv is not None else sys.argv[1:])
    conn = db.connect()
    if args.cmd == "add":
        new = db.add(conn, Expense(args.amount, args.category, args.note, args.day))
        print(f"added #{new}", file=out)
    elif args.cmd == "list":
        for e in db.expenses(conn, args.month, args.category):
            print(f"#{e.id} {e.day} {e.category:10} {e.amount:8.2f} {e.note}", file=out)
    elif args.cmd == "summary":
        for category, total in sorted(db.summary(conn, args.month).items()):
            print(f"{category:10} {total:8.2f}", file=out)
    elif args.cmd == "import":
        rows = importers.from_csv(args.csv)
        for e in rows:
            db.add(conn, e)
        print(f"imported {len(rows)}", file=out)
    elif args.cmd == "report":
        month = args.month
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(report.html(month, db.expenses(conn, month), db.summary(conn, month)))
        print(f"wrote {args.html}", file=out)
