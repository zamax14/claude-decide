"""Servidor local con el modelo cargado una vez: puntúa el catálogo para cada petición.

    python daemon.py            # escucha en 127.0.0.1:7717 (CLAUDE_DECIDE_PORT)
    curl -s localhost:7717/score -d '{"prompt": "haz commit", "cwd": "."}'

Cada decisión se apunta en logs/decisions.jsonl para compararla después con lo que usó Claude.
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import catalog
import scorer
from model import load_model

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("CLAUDE_DECIDE_PORT", 7717))
LOG = ROOT / "logs" / "decisions.jsonl"
# ponytail: 0,5 = el «sí» habitual del elemento. Con Laya ningún umbral separa «hola» de una petición
# real (bench: la máxima de las negativas es 0,90), así que en la práctica manda TOP_K.
THRESHOLD = .5
TOP_K = 5


def decide(model, prior, prompt, cwd):
    start = time.perf_counter()
    items = catalog.build(cwd)
    scorer.calibrate(model, items, prior)  # Solo calcula lo nuevo; con el catálogo sin cambios no cuesta nada.
    ranked = scorer.rank(model, items, prompt, prior)
    out = {"model": model.name, "selected": scorer.select(ranked, THRESHOLD, TOP_K), "ranking": ranked,
           "ms": round(1000 * (time.perf_counter() - start))}
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as log:
        log.write(json.dumps({"ts": time.time(), "cwd": str(cwd), "prompt": prompt[:scorer.MAX_PROMPT],
                              "selected": [r["id"] for r in out["selected"]], "top": ranked[:TOP_K],
                              "ms": out["ms"]}, ensure_ascii=False) + "\n")
    return out


def handler(model, prior):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # La consola queda para errores reales.
            pass

        def reply(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(200 if self.path == "/health" else 404, {"model": model.name})

        def do_POST(self):
            if self.path != "/score":
                return self.reply(404, {"error": "ruta desconocida"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                self.reply(200, decide(model, prior, str(body.get("prompt", "")), Path(body.get("cwd") or Path.home())))
            except Exception as exc:  # El hook trata cualquier error como «no inyectar nada».
                self.reply(500, {"error": f"{type(exc).__name__}: {exc}"})
    return Handler


def main():
    model = load_model()
    # Solo en local: el prompt del usuario no debe salir de la máquina.
    server = HTTPServer(("127.0.0.1", PORT), handler(model, scorer.load_prior()))
    print(f"claude-decide: {model.name} escuchando en 127.0.0.1:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
