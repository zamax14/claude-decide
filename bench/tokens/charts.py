"""Gráficas (en inglés, para el README) de results.jsonl en bench/tokens/charts/: contexto fijo, tokens por turno y por tarea.

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
DEFAULT = "claude-haiku-4-5-20251001"  # Las gráficas del README; otro modelo lleva su nombre en el archivo.
MODEL = os.environ.get("BENCH_MODEL", DEFAULT)
TAG = "" if MODEL == DEFAULT else "-" + MODEL.removeprefix("claude-")
SIN, CON = "#eb6834", "#2a78d6"  # Paleta validada (dataviz): naranja sin, azul con.
ENV = "25 skills, 10 agents and 5 rules"
WITHOUT, WITH = "without claude-decide", "with claude-decide"


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
    for ax, values, title in ((axes[0], fixed, "Context of one message"),
                              (axes[1], per_turn, "Tokens per turn, real tasks")):
        if not (values["sin"] and values["con"]):
            ax.set_visible(False)
            continue
        a, b = mean(values["sin"]), mean(values["con"])
        ax.bar([0, 1], [a, b], .6, color=[SIN, CON], edgecolor="white", linewidth=2)
        ax.text(0, a, f"{a / 1000:.1f}k", ha="center", va="bottom")
        ax.text(1, b, f"{b / 1000:.1f}k ({(b - a) / a:+.0%})", ha="center", va="bottom", fontweight="bold")
        ax.set_xticks([0, 1], [WITHOUT, WITH])
        ax.set_ylim(0, max(a, b) * 1.2)
        style(ax, title, "tokens (input + cache + output)")
    fig.suptitle(f"{MODEL} · project with {ENV}", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / f"summary{TAG}.png", dpi=160)


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
    ax.bar([i - .2 for i in x], sin, .4, color=SIN, edgecolor="white", linewidth=2, label=WITHOUT)
    ax.bar([i + .2 for i in x], con, .4, color=CON, edgecolor="white", linewidth=2, label=WITH)
    for i, (a, b) in enumerate(zip(sin, con)):
        ax.text(i + .2, b, f"{(b - a) / a:+.0%}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(list(x), [f"{t}\n~{n:.0f} turns\n{p}" for t, n, p in zip(tasks, turns, passed)], fontsize=7)
    ns = [len(groups[(t, c)]) for t in tasks for c in ("sin", "con")]
    runs = f"{min(ns)}" if min(ns) == max(ns) else f"{min(ns)}–{max(ns)}"
    style(ax, f"Tokens per task (mean of {runs} runs; below, solved without · with)",
          "thousand tokens (input + cache + output)")
    ax.legend(frameon=False)
    fig.suptitle(f"{MODEL} · project with {ENV}", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / f"tasks{TAG}.png", dpi=160)


def main():
    OUT.mkdir(exist_ok=True)
    groups = load()
    summary(groups)
    per_task(groups)
    print("\n".join(str(p) for p in sorted(OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
