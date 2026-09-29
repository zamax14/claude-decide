"""Servidor local con el modelo cargado una vez: puntúa el catálogo y arma el contexto para Claude.

    python daemon.py            # escucha en 127.0.0.1:7717 (CLAUDE_DECIDE_PORT)
    curl -s localhost:7717/score -d '{"event": "UserPromptSubmit", "prompt": "haz commit", "cwd": "."}'

Con cada petición del usuario puntúa todo; con cada herramienta que usa Claude (PostToolUse) vuelve
a puntuar con el paso en curso y solo manda lo que aún no mandó en esa petición.
Cada decisión se apunta en logs/decisions.jsonl para compararla después con lo que usó Claude.
"""
import json
import os
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import catalog
import mcp_catalog
import scorer
from model import load_model

PORT = int(os.environ.get("CLAUDE_DECIDE_PORT", 7717))
# ponytail: 0,5 = el «sí» habitual del elemento, o la probabilidad con un modelo entrenado para esta tarea.
# Con Laya base ningún umbral separa «hola» de una petición real (bench: máxima de las negativas 0,90) y
# manda TOP_K; con laya-context_prefilter la máxima es 0,06 y el umbral decide.
THRESHOLD = .5
TOP_K = 5
STEP_K = 2  # En cada herramienta, como mucho dos novedades: se llama muchas veces por petición.
MAX_RULE = 4000  # caracteres de una rule de la librería que se inyectan
MAX_SESSIONS = 50


def line(item, score):
    """Una línea por elemento elegido: lo que Claude no tiene ya en su contexto."""
    head = f"- {item['tipo']} `{item['nombre']}` ({score:.2f})"
    if item["gestion"] == "name-only":
        fields, _ = catalog.frontmatter(Path(item["ruta"]).read_text(encoding="utf-8", errors="replace"))
        return f"{head}: {' '.join(fields.get('description', item['descripcion']).split())}"
    if item["gestion"] == "library":
        _, body = catalog.frontmatter(Path(item["ruta"]).read_text(encoding="utf-8", errors="replace"))
        return f"{head}, a rule to follow:\n{body.strip()[:MAX_RULE]}"
    if item["tipo"] == "mcp":
        text = item["descripcion"] if len(item["descripcion"]) <= 200 else item["descripcion"][:200].rsplit(" ", 1)[0] + "…"
        return f"{head}: {text} Load it with ToolSearch `select:{item['nombre']}`."
    return head


def step_input(tool_input):
    """Lo principal de la entrada de la herramienta, como en el entrenamiento: «Bash npm test», «Edit src/a.ts»."""
    if isinstance(tool_input, dict) and tool_input:
        tool_input = next(iter(tool_input.values()))  # command, file_path, pattern, url…
    text = tool_input if isinstance(tool_input, str) else json.dumps(tool_input, ensure_ascii=False)
    return " ".join(text.split())[:300]


def context(chosen, items, step=None):
    if not chosen:
        return ""
    by_id = {i["id"]: i for i in items}
    intro = (f"claude-decide: for the current step ({step}) these also fit:" if step else
             "claude-decide scored skills, rules, agents and MCP tools for this request. The best fits:")
    outro = ("\nThe other skills are still available by name."
             if any(i["gestion"] == "name-only" for i in items) and not step else "")
    return "\n".join([intro, *(line(by_id[r["id"]], r["score"]) for r in chosen)]) + outro


class Decider:
    def __init__(self, model, prior, home=catalog.HOME, data=catalog.DATA, claude_json=mcp_catalog.CLAUDE_JSON):
        self.model, self.prior = model, prior
        self.home, self.data, self.claude_json = home, data, claude_json
        self.sessions = OrderedDict()  # session_id → {"prompt", "sent"}

    def session(self, session_id):
        state = self.sessions.pop(session_id, None) or {"prompt": "", "sent": set()}
        self.sessions[session_id] = state
        while len(self.sessions) > MAX_SESSIONS:
            self.sessions.popitem(last=False)
        return state

    def decide(self, event):
        start = time.perf_counter()
        items = catalog.build(event.get("cwd") or Path.home(), self.home, self.data, self.claude_json)
        scorer.calibrate(self.model, items, self.prior, self.data / "prior.json")  # Solo lo nuevo.
        state = self.session(event.get("session_id", ""))
        step = None
        if event.get("event") == "PostToolUse":
            step = f"{event.get('tool_name', '')} {step_input(event.get('tool_input', ''))}"
            query = f"{state['prompt']}\nCurrent step: {step}".strip()
        else:
            state["prompt"], state["sent"] = event.get("prompt", ""), set()
            query = state["prompt"]
        ranked = scorer.rank(self.model, items, state["prompt"], self.prior, step or "")
        chosen = [r for r in scorer.select(ranked, THRESHOLD, len(ranked)) if r["id"] not in state["sent"]]
        chosen = chosen[:STEP_K if step else TOP_K]
        state["sent"].update(r["id"] for r in chosen)
        out = {"model": self.model.name, "selected": chosen, "context": context(chosen, items, step and step[:80]),
               "ms": round(1000 * (time.perf_counter() - start))}
        self.log(event, query, out, ranked)
        return out

    def log(self, event, query, out, ranked):
        path = self.data / "logs" / "decisions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log:
            log.write(json.dumps({"ts": time.time(), "event": event.get("event"), "session": event.get("session_id"),
                                  "cwd": str(event.get("cwd")), "query": query[:scorer.MAX_PROMPT],
                                  "selected": [r["id"] for r in out["selected"]], "top": ranked[:TOP_K],
                                  "ms": out["ms"]}, ensure_ascii=False) + "\n")


def handler(decider):
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
            self.reply(200 if self.path == "/health" else 404, {"model": decider.model.name})

        def do_POST(self):
            if self.path != "/score":
                return self.reply(404, {"error": "ruta desconocida"})
            try:
                event = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                self.reply(200, decider.decide(event))  # HTTPServer atiende en serie: un modelo, un estado.
            except Exception as exc:  # El hook trata cualquier error como «no inyectar nada».
                self.reply(500, {"error": f"{type(exc).__name__}: {exc}"})
    return Handler


def refresh_mcp():
    """Caché de tools MCP de todos los proyectos conocidos; los servidores lentos no frenan el arranque."""
    configs = mcp_catalog.servers(Path.home())
    projects = catalog.read_json(mcp_catalog.CLAUDE_JSON).get("projects", {})
    for cwd in projects:
        configs.update(mcp_catalog.servers(cwd))
    mcp_catalog.refresh(catalog.DATA / "mcp_tools.json", configs)


def main():
    threading.Thread(target=refresh_mcp, daemon=True).start()
    model = load_model()
    decider = Decider(model, scorer.load_prior(catalog.DATA / "prior.json"))
    # Solo en local: el prompt del usuario no debe salir de la máquina.
    server = HTTPServer(("127.0.0.1", PORT), handler(decider))
    print(f"claude-decide: {model.name} listening on 127.0.0.1:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
