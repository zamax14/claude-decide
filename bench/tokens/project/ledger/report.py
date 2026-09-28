from html import escape


def html(month, expenses, totals):
    rows = "\n".join(f"<tr><td>{e.day}</td><td>{escape(e.category)}</td><td>{escape(e.note)}</td>"
                     f"<td>{e.amount:.2f}</td></tr>" for e in expenses)
    summary = "\n".join(f"<li>{escape(c)}: {t:.2f}</li>" for c, t in sorted(totals.items()))
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Expenses {month}</title></head>
<body>
<h1>Expenses {month}</h1>
<ul>{summary}</ul>
<table>
<tr><th>Day</th><th>Category</th><th>Note</th><th>Amount</th></tr>
{rows}
</table>
</body></html>
"""
