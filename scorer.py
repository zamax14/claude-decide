"""Puntúa cada elemento del catálogo frente a una petición: una pregunta `noul` por elemento.

Estado = la petición (y el paso en curso); preguntas = una por elemento, todas en la misma llamada
(Laya las resuelve en una pasada). Como en el Atlas de Text-Decision-Benchmark, a cada elemento se le
resta su «sí» medio sobre peticiones de calibración: algunos dicen «sí» a casi todo y sin eso copan el
ranking. Un modelo entrenado para esta tarea (`model.prefilter`) lee el estado como lo vio al entrenar,
{"request", "step"}, y no lleva prior: su probabilidad ya está calibrada.
"""
import hashlib
import json
import math

MAX_PROMPT = 1500  # ponytail: caracteres; basta para la intención. Lo que no quepa en contexto lo rechaza el modelo.
BATCH = 8  # Como el Atlas: con 32 una GPU de 6 GB se queda sin memoria y Laya pasa a CPU para siempre.
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


def state_for(model, prompt, step=""):
    if getattr(model, "prefilter", False):
        return {"request": prompt[:MAX_PROMPT], "step": step}
    return f"{prompt}\nCurrent step: {step}".strip()[:MAX_PROMPT] if step else prompt[:MAX_PROMPT]


def raw_scores(model, items, prompt, step=""):
    """Probabilidad de «sí» de cada elemento para esta petición (y paso), por lotes."""
    state, out = state_for(model, prompt, step), {}
    for start in range(0, len(items), BATCH):
        chunk = items[start:start + BATCH]
        answers = model.predict(state, {i["id"]: question(i) for i in chunk})["answers"]
        out.update({i["id"]: float(answers[i["id"]]["noul"]) for i in chunk})
    return out


def prior_key(model, item):
    return hashlib.sha1(f"{model.name}\n{QUESTION.format(**item)}".encode()).hexdigest()[:16]


def load_prior(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def calibrate(model, items, prior, path=None):
    """Completa el prior de los elementos nuevos o cambiados; devuelve si hubo que calcular algo."""
    if getattr(model, "prefilter", False):
        return False  # Entrenado para esto: con prior, «hola» vuelve a puntuar alto (bench: 0,06 → 0,57).
    missing = [i for i in items if prior_key(model, i) not in prior]
    if not missing:
        return False
    sums = dict.fromkeys((i["id"] for i in missing), 0.0)
    for prompt in CALIBRATION:
        for item_id, p in raw_scores(model, missing, prompt).items():
            sums[item_id] += logit(p) / len(CALIBRATION)
    prior.update({prior_key(model, i): round(sums[i["id"]], 3) for i in missing})
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(prior, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return True


def rank(model, items, prompt, prior, step=""):
    """Todos los elementos ordenados por puntuación relativa (0–1, 0,5 = su «sí» habitual; sin prior, la
    probabilidad del modelo)."""
    raw = raw_scores(model, items, prompt, step)
    ranked = []
    for i in items:
        relative = 1 / (1 + math.exp(-(logit(raw[i["id"]]) - prior.get(prior_key(model, i), 0.0))))
        ranked.append({"id": i["id"], "tipo": i["tipo"], "nombre": i["nombre"],
                       "score": round(relative, 4), "raw": round(raw[i["id"]], 4)})
    return sorted(ranked, key=lambda r: -r["score"])


def select(ranked, threshold, top_k):
    return [r for r in ranked if r["score"] >= threshold][:top_k]
