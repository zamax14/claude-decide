"""Puntúa cada elemento del catálogo frente a una petición: una pregunta `noul` por elemento.

Estado = la petición; preguntas = una por elemento, todas en la misma llamada (Laya las resuelve
en una pasada). Como en el Atlas de Text-Decision-Benchmark, a cada elemento se le resta su «sí»
medio sobre peticiones de calibración: algunos dicen «sí» a casi todo y sin eso copan el ranking.
"""
import hashlib
import json
import math
from pathlib import Path

MAX_PROMPT = 1500  # ponytail: caracteres; basta para la intención. Lo que no quepa en contexto lo rechaza el modelo.
BATCH = 8  # Como el Atlas: con 32 una GPU de 6 GB se queda sin memoria y Laya pasa a CPU para siempre.
PRIOR_PATH = Path(__file__).resolve().parent / "prior.json"
QUESTION = "Is this Claude Code {tipo} useful for handling the user's request? {tipo} «{nombre}»: {descripcion}"
# Peticiones variadas para medir cuánto dice «sí» cada elemento sin importar lo que se pida.
# No coinciden con las de bench/cases.json, para no calibrar sobre ellas.
CALIBRATION = ("hola, ¿qué tal?", "explícame qué hace esta función", "añade un endpoint GET /users a la API",
               "renombra la variable total a subtotal en todo el archivo", "¿por qué falla este import en Python?",
               "escribe un script que lea un CSV y sume una columna", "optimiza esta consulta SQL",
               "traduce el README al inglés", "actualiza la versión de la dependencia requests",
               "¿cuál es la diferencia entre let y const?")


def question(item):
    return {"type": "noul", "instructions": QUESTION.format(**item)}


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def raw_scores(model, items, prompt):
    """Probabilidad de «sí» de cada elemento para esta petición, por lotes."""
    state, out = prompt[:MAX_PROMPT], {}
    for start in range(0, len(items), BATCH):
        chunk = items[start:start + BATCH]
        answers = model.predict(state, {i["id"]: question(i) for i in chunk})["answers"]
        out.update({i["id"]: float(answers[i["id"]]["noul"]) for i in chunk})
    return out


def prior_key(model, item):
    return hashlib.sha1(f"{model.name}\n{QUESTION.format(**item)}".encode()).hexdigest()[:16]


def load_prior(path=PRIOR_PATH):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def calibrate(model, items, prior, path=PRIOR_PATH):
    """Completa el prior de los elementos nuevos o cambiados; devuelve si hubo que calcular algo."""
    missing = [i for i in items if prior_key(model, i) not in prior]
    if not missing:
        return False
    sums = dict.fromkeys((i["id"] for i in missing), 0.0)
    for prompt in CALIBRATION:
        for item_id, p in raw_scores(model, missing, prompt).items():
            sums[item_id] += logit(p) / len(CALIBRATION)
    prior.update({prior_key(model, i): round(sums[i["id"]], 3) for i in missing})
    if path:
        path.write_text(json.dumps(prior, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return True


def rank(model, items, prompt, prior):
    """Todos los elementos ordenados por puntuación relativa (0–1, 0,5 = su «sí» habitual)."""
    raw = raw_scores(model, items, prompt)
    ranked = []
    for i in items:
        relative = 1 / (1 + math.exp(-(logit(raw[i["id"]]) - prior.get(prior_key(model, i), 0.0))))
        ranked.append({"id": i["id"], "tipo": i["tipo"], "nombre": i["nombre"],
                       "score": round(relative, 4), "raw": round(raw[i["id"]], 4)})
    return sorted(ranked, key=lambda r: -r["score"])


def select(ranked, threshold, top_k):
    return [r for r in ranked if r["score"] >= threshold][:top_k]
