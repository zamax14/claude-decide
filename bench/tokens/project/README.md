# ledger

Personal expense tracker: a small command-line tool over SQLite. No dependencies beyond the standard library.

```bash
python -m ledger add 12.50 food "lunch with Ana"
python -m ledger list --month 2026-09
python -m ledger summary --month 2026-09
python -m ledger import bank.csv
python -m ledger report --month 2026-09 --html report.html
```

The database lives in `~/.ledger.db`, or wherever `LEDGER_DB` points.

## Development

```bash
python -m unittest discover -s tests
```
