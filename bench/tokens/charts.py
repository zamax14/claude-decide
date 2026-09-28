"""Gráficas de results.jsonl en bench/tokens/charts/: contexto fijo por mensaje y tokens por tarea.

    python3 bench/tokens/charts.py      # necesita matplotlib
"""
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "charts"
# ponytail: las primeras ejecuciones de «commit» fallaron por falta de identidad de git en la copia; fuera
# hasta repetirlas.
EXCLUDE = {"commit"}
SCENARIOS = {"setup": "Tu setup (4 skills recortables)", "saturado": "Saturado (100 skills, 50 agentes, 15 rules)"}
SIN, CON = "#9aa5b1", "#2f7ed8"


def total(r):
    return r["input"] + r["cache_read"] + r["cache_write"] + r["output"]


def load():
    groups = defaultdict(list)
    for line in (HERE / "results.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["task"] not in EXCLUDE:
            groups[(r["scenario"], r["task"], r["condition"])].append(r)
    return groups


def fixed_context(groups):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    labels, x = [], 0
    for scenario, name in SCENARIOS.items():
        sin, con = groups.get((scenario, "ok", "sin")), groups.get((scenario, "ok", "con"))
        if not (sin and con):
            continue
        a, b = mean(map(total, sin)), mean(map(total, con))
        ax.bar(x - .2, a, .4, color=SIN, label="sin claude-decide" if not labels else None)
        ax.bar(x + .2, b, .4, color=CON, label="con claude-decide" if not labels else None)
        ax.text(x - .2, a, f"{a / 1000:.1f}k", ha="center", va="bottom")
        ax.text(x + .2, b, f"{b / 1000:.1f}k\n({(b - a) / a:+.0%})", ha="center", va="bottom", fontweight="bold")
        labels.append(name)
        x += 1
    ax.set_xticks(range(len(labels)), labels)
    ax.set_ylabel("tokens de un mensaje de un turno")
    ax.set_title("Contexto fijo que Claude Code lee en cada mensaje")
    ax.set_ylim(0, ax.get_ylim()[1] * 1.2)
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "contexto_fijo.png", dpi=160)


def per_task(groups, scenario):
    tasks = sorted({t for s, t, _ in groups if s == scenario and t != "ok"
                    and (s, t, "sin") in groups and (s, t, "con") in groups})
    if not tasks:
        return
    sin = [mean(map(total, groups[(scenario, t, "sin")])) / 1000 for t in tasks]
    con = [mean(map(total, groups[(scenario, t, "con")])) / 1000 for t in tasks]
    turns = [mean(r["turns"] or 0 for c in ("sin", "con") for r in groups[(scenario, t, c)]) for t in tasks]
    fig, ax = plt.subplots(figsize=(10, 4.8))
    x = range(len(tasks))
    ax.bar([i - .2 for i in x], sin, .4, color=SIN, label="sin claude-decide")
    ax.bar([i + .2 for i in x], con, .4, color=CON, label="con claude-decide")
    for i, (a, b) in enumerate(zip(sin, con)):
        ax.text(i + .2, b, f"{(b - a) / a:+.0%}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(list(x), [f"{t}\n~{n:.0f} turnos" for t, n in zip(tasks, turns)], fontsize=8)
    ax.set_ylabel("miles de tokens por tarea (entrada + caché + salida)")
    ns = [len(groups[(scenario, t, c)]) for t in tasks for c in ("sin", "con")]
    runs = f"{min(ns)}" if min(ns) == max(ns) else f"{min(ns)}–{max(ns)}"
    ax.set_title(f"{SCENARIOS[scenario]}: tokens por tarea (media de {runs} ejecuciones)")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / f"tareas_{scenario}.png", dpi=160)


def main():
    OUT.mkdir(exist_ok=True)
    groups = load()
    fixed_context(groups)
    for scenario in SCENARIOS:
        per_task(groups, scenario)
    print("\n".join(str(p) for p in sorted(OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
