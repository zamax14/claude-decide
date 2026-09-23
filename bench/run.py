"""Acierto del ranking sobre bench/cases.json, por variante de pregunta, con y sin prior.

    python bench/run.py [cwd] [--todo]

hit@k: algún elemento correcto entre los k primeros. recall@5: fracción de los correctos en el top 5.
«negativas»: puntuación máxima en peticiones donde no debería saltar nada (más baja, mejor).
"""
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import catalog  # noqa: E402
import model as models  # noqa: E402
import scorer  # noqa: E402

CASES = json.loads((ROOT / "bench" / "cases.json").read_text(encoding="utf-8"))
QUESTIONS = {
    "en": scorer.QUESTION,
    "es": "¿Le sirve a Claude Code esta {tipo} para atender la petición del usuario? {tipo} «{nombre}»: {descripcion}",
    "en-pide": "Does the user's request ask for what the {tipo} «{nombre}» covers? {descripcion}",
    "en-tarea": "Task: {descripcion} Is the user's message asking for this task?",
}


def per_item(model, items, prompt):
    """Disposición del Atlas: estado = el elemento, pregunta = la petición; una llamada por elemento."""
    ask = {"q": {"type": "noul", "instructions": f"Is this useful for the request: {prompt[:scorer.MAX_PROMPT]}"}}
    return {i["id"]: float(model.predict(f"Claude Code {i['tipo']} «{i['nombre']}»: {i['descripcion']}",
                                         ask)["answers"]["q"]["noul"]) for i in items}


def evaluate(model, items, use_prior):
    prior = {}
    if use_prior:
        scorer.calibrate(model, items, prior, path=None)
    hits1, hits3, recalls, mrr, negatives, times = [], [], [], [], [], []
    for case in CASES:
        start = time.perf_counter()
        ranking = scorer.rank(model, items, case["q"], prior)
        times.append(time.perf_counter() - start)
        if not case["gold"]:
            negatives.append(ranking[0]["score"])
            continue
        ranked = [r["id"] for r in ranking]
        gold = set(case["gold"])
        hits1.append(ranked[0] in gold)
        hits3.append(bool(gold & set(ranked[:3])))
        recalls.append(len(gold & set(ranked[:5])) / len(gold))
        mrr.append(1 / (1 + next(n for n, r in enumerate(ranked) if r in gold)))
    mean = statistics.mean
    return {"hit@1": mean(hits1), "hit@3": mean(hits3), "recall@5": mean(recalls), "mrr": mean(mrr),
            "negativas": mean(negatives), "ms": 1000 * mean(times)}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    items = catalog.build(args[0] if args else Path.home())
    missing = {g for c in CASES for g in c["gold"]} - {i["id"] for i in items}
    if missing:
        sys.exit(f"Casos con elementos que no están en el catálogo: {sorted(missing)}")
    model = models.load_model()
    batched = scorer.raw_scores
    variants = [(f"una pasada, pregunta {lang}", batched, q) for lang, q in QUESTIONS.items()]
    if "--todo" in sys.argv:  # Tres veces más lento; en la primera medición no ganó.
        variants.append(("un elemento por llamada", lambda m, it, p: per_item(m, it, p), "atlas {tipo} {nombre} {descripcion}"))
    print(f"{len(items)} elementos, {len(CASES)} peticiones, modelo {model.name} en {model.agent.device}\n", flush=True)
    print(f"{'variante':32} {'prior':5} {'hit@1':>6} {'hit@3':>6} {'rec@5':>6} {'mrr':>6} {'neg':>6} {'ms':>6}")
    for name, raw, question in variants:
        scorer.raw_scores, scorer.QUESTION = raw, question
        for use_prior in (False, True):
            r = evaluate(model, items, use_prior)
            print(f"{name:32} {'sí' if use_prior else 'no':5} {r['hit@1']:6.2f} {r['hit@3']:6.2f} "
                  f"{r['recall@5']:6.2f} {r['mrr']:6.2f} {r['negativas']:6.2f} {r['ms']:6.0f} {model.agent.device}", flush=True)
    scorer.raw_scores = batched


if __name__ == "__main__":
    main()
