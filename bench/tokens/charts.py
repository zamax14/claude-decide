"""Gráficas de results.jsonl en bench/tokens/charts/: contexto fijo, tokens por turno y tokens por tarea.

    python3 bench/tokens/charts.py      # necesita matplotlib; BENCH_MODEL elige el modelo (Haiku por defecto)
"""
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "charts"
MODEL = os.environ.get("BENCH_MODEL", "claude-haiku-4-5-20251001")
SIN, CON = "#9aa5b1", "#2f7ed8"
ENV = "25 skills, 10 agentes y 5 rules"


def total(r):
    return r["input"] + r["cache_read"] + r["cache_write"] + r["output"]


def load():
    groups = defaultdict(list)
    for line in (HERE / "results.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["model"] == MODEL:
            groups[(r["task"], r["condition"])].append(r)
    return groups


def style(ax, title, ylabel):
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.spines[["top", "right"]].set_visible(False)


def summary(groups):
    """Dos paneles: el contexto de un mensaje de un turno y los tokens por turno en las tareas reales."""
    per_turn = {c: [total(r) / (r["turns"] or 1) for (t, cc), rs in groups.items() if cc == c and t != "ok" for r in rs]
                for c in ("sin", "con")}
    fixed = {c: [total(r) for r in groups.get(("ok", c), [])] for c in ("sin", "con")}
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    for ax, values, title in ((axes[0], fixed, "Contexto de un mensaje («responde solo: ok»)"),
                              (axes[1], per_turn, "Tokens por turno, tareas reales")):
        if not (values["sin"] and values["con"]):
            ax.set_visible(False)
            continue
        a, b = mean(values["sin"]), mean(values["con"])
        ax.bar([0, 1], [a, b], .6, color=[SIN, CON])
        ax.text(0, a, f"{a / 1000:.1f}k", ha="center", va="bottom")
        ax.text(1, b, f"{b / 1000:.1f}k ({(b - a) / a:+.0%})", ha="center", va="bottom", fontweight="bold")
        ax.set_xticks([0, 1], ["sin claude-decide", "con claude-decide"])
        ax.set_ylim(0, max(a, b) * 1.2)
        style(ax, title, "tokens (entrada + caché + salida)")
    fig.suptitle(f"{MODEL} · proyecto con {ENV}", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / "resumen.png", dpi=160)


def per_task(groups):
    tasks = sorted({t for t, _ in groups if t != "ok" and (t, "sin") in groups and (t, "con") in groups})
    if not tasks:
        return
    sin = [mean(map(total, groups[(t, "sin")])) / 1000 for t in tasks]
    con = [mean(map(total, groups[(t, "con")])) / 1000 for t in tasks]
    turns = [mean(r["turns"] or 0 for c in ("sin", "con") for r in groups[(t, c)]) for t in tasks]
    passed = [f"{sum(r['passed'] for r in groups[(t, 'sin')])}/{len(groups[(t, 'sin')])} · "
              f"{sum(r['passed'] for r in groups[(t, 'con')])}/{len(groups[(t, 'con')])}" for t in tasks]
    fig, ax = plt.subplots(figsize=(10, 4.8))
    x = range(len(tasks))
    ax.bar([i - .2 for i in x], sin, .4, color=SIN, label="sin claude-decide")
    ax.bar([i + .2 for i in x], con, .4, color=CON, label="con claude-decide")
    for i, (a, b) in enumerate(zip(sin, con)):
        ax.text(i + .2, b, f"{(b - a) / a:+.0%}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(list(x), [f"{t}\n~{n:.0f} turnos\n{p}" for t, n, p in zip(tasks, turns, passed)], fontsize=7)
    ns = [len(groups[(t, c)]) for t in tasks for c in ("sin", "con")]
    runs = f"{min(ns)}" if min(ns) == max(ns) else f"{min(ns)}–{max(ns)}"
    style(ax, f"Tokens por tarea (media de {runs} ejecuciones; debajo, resueltas sin · con)",
          "miles de tokens (entrada + caché + salida)")
    ax.legend(frameon=False)
    fig.suptitle(f"{MODEL} · proyecto con {ENV}", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / "tareas.png", dpi=160)


def main():
    OUT.mkdir(exist_ok=True)
    groups = load()
    summary(groups)
    per_task(groups)
    print("\n".join(str(p) for p in sorted(OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
