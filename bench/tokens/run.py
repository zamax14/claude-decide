"""Tokens que gasta Claude Code con y sin claude-decide, resolviendo tareas reales sobre un proyecto de ejemplo.

    python bench/tokens/run.py                      # todo, retomando lo que ya esté en results.jsonl
    python bench/tokens/run.py --only=ok --reps=1   # algunas tareas (separadas por comas) y repeticiones
    python bench/tokens/run.py --report             # solo la tabla

Un entorno normal: el proyecto con lo que instalaría alguien que añadió un par de colecciones (25 skills,
10 agentes y 5 rules públicas en su .claude/, la mitad relacionadas con el proyecto), sin nada del usuario
(--setting-sources project,local). Sin plugin, Claude lo carga todo; con plugin, las skills pasan a «solo
nombre» y las rules a la librería, como haría `library.py apply` y `manage`, y el hook le devuelve lo elegido.

Cada ejecución es un `claude -p` sobre una copia limpia de `project/`, con un daemon propio en otro puerto con
el modelo de CLAUDE_DECIDE_MODEL. Después, la comprobación de la tarea dice si quedó resuelta.
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = HERE / ".cache"
RESULTS = HERE / "results.jsonl"
TASKS = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))
MODEL = os.environ.get("BENCH_MODEL", "claude-haiku-4-5-20251001")
REPS, PARALLEL, MAX_TURNS, TIMEOUT = 3, 3, 15, 900
PYTHON = os.environ.get("CLAUDE_DECIDE_PYTHON", sys.executable)  # Con laya y torch, para el daemon.
PORT = 7719
N_SKILLS, N_AGENTS, N_RULES = 25, 10, 5
# La mitad de cada tipo, de lo que encaja con este proyecto; la otra mitad, al azar.
RELATED = re.compile(r"python|git|test|sql|docker|github-actions|\bci\b|docs?\b|readme|refactor|security|debug|code-review", re.I)
SOURCES = {  # Repos con licencia permisiva, clonados en .cache (git clone --depth 1).
    "agents": "https://github.com/wshobson/agents.git",                             # MIT
    "claude-code-templates": "https://github.com/davila7/claude-code-templates.git",  # MIT
    "awesome-cursorrules": "https://github.com/PatrickJS/awesome-cursorrules.git",    # CC0
}
ENV_DIR, HOME, DATA = CACHE / "normal", CACHE / "normal-home", CACHE / "normal-data"

CHECK_PRELUDE = """
import io, os, sys, tempfile, unittest
sys.path.insert(0, '.')
os.environ['LEDGER_DB'] = os.path.join(tempfile.mkdtemp(), 'check.db')
def tests():
    result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.discover('tests'))
    assert result.wasSuccessful(), 'the tests fail'
def cli(*argv):
    from ledger import cli as c
    out = io.StringIO()
    c.main(list(argv), out)
    return out.getvalue()
"""


def frontmatter_description(text):
    head = text.split("---")[1] if text.startswith("---") else ""
    return next((l.split(":", 1)[1].strip() for l in head.splitlines() if l.startswith("description:")), "")


def pick(rng, candidates, n):
    """n de `candidates` (nombre → ruta): la mitad relacionadas con el proyecto por su nombre, el resto al azar."""
    names = sorted(candidates)
    related = [c for c in names if RELATED.search(c)]
    chosen = rng.sample(related, min(n // 2, len(related)))
    return chosen + rng.sample([c for c in names if c not in chosen], n - len(chosen))


def environment():
    """.cache/normal/{skills,agents,rules}: lo mismo en cada ejecución (semilla fija)."""
    if ENV_DIR.exists():
        return ENV_DIR
    for name, url in SOURCES.items():
        if not (CACHE / name).exists():
            subprocess.run(["git", "clone", "-q", "--depth", "1", url, str(CACHE / name)], check=True)
    read = lambda p: p.read_text(encoding="utf-8", errors="replace")
    rng = random.Random(0)
    skills = {p.parent.name: p for repo in ("agents", "claude-code-templates")
              for p in sorted((CACHE / repo).rglob("SKILL.md")) if frontmatter_description(read(p))}
    agents = {p.stem: p for repo in ("agents", "claude-code-templates") for p in sorted((CACHE / repo).rglob("agents/*.md"))
              if frontmatter_description(read(p))}
    rules = {f"{p.parent.name}-{p.stem}"[-80:]: p for p in sorted((CACHE / "awesome-cursorrules").rglob("*.mdc"))}
    for name in pick(rng, skills, N_SKILLS):
        shutil.copytree(skills[name].parent, ENV_DIR / "skills" / name)
    (ENV_DIR / "agents").mkdir(parents=True)
    for name in pick(rng, agents, N_AGENTS):
        shutil.copy(agents[name], ENV_DIR / "agents" / f"{name}.md")
    (ENV_DIR / "rules").mkdir()
    for name in pick(rng, rules, N_RULES):
        body = read(rules[name])
        body = body.split("---", 2)[2] if body.startswith("---") else body  # Sin los globs de Cursor: siempre activas.
        (ENV_DIR / "rules" / f"{name}.md").write_text(body.strip() + "\n", encoding="utf-8")
    return ENV_DIR


def conditions(env):
    """Los argumentos de `claude -p` y el entorno de cada condición; prepara el daemon del plugin."""
    if not HOME.exists():
        shutil.copytree(env / "rules", HOME / "library" / "rules")
    DATA.mkdir(exist_ok=True)
    skills = sorted(p.name for p in (env / "skills").iterdir())
    (DATA / "state.json").write_text(json.dumps({"skills": dict.fromkeys(skills), "rules": []}))
    (HOME / "settings.json").write_text(json.dumps({"skillOverrides": dict.fromkeys(skills, "name-only")}))
    isolated = ["--setting-sources", "project,local", "--strict-mcp-config"]
    return {"sin": (isolated, {}),
            "con": (isolated + ["--plugin-dir", str(ROOT), "--settings", str(HOME / "settings.json")],
                    {"CLAUDE_DECIDE_PORT": str(PORT), "CLAUDE_DECIDE_HOME": str(DATA), "CLAUDE_DECIDE_MODE": "inject"})}


def start_daemon():
    """El daemon del plugin, con esta configuración: calentado antes, o el hook pasaría de su límite de 3 s."""
    env = {**os.environ, "CLAUDE_DECIDE_PORT": str(PORT), "CLAUDE_DECIDE_HOME": str(DATA), "CLAUDE_CONFIG_DIR": str(HOME)}
    log = open(CACHE / "daemon-normal.log", "a")
    proc = subprocess.Popen([PYTHON, str(ROOT / "daemon.py")], cwd=ROOT, env=env, stdout=log, stderr=log)
    for _ in range(180):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2)
            break
        except OSError:
            time.sleep(1)
    else:
        raise SystemExit(f"el daemon no arrancó: mira {CACHE}/daemon-normal.log")
    warm = json.dumps({"event": "UserPromptSubmit", "prompt": "hola", "cwd": str(HERE / "project")}).encode()
    for attempt in range(2):  # La primera arma el catálogo; la segunda mide lo que verá el hook.
        start = time.time()
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/score", warm), timeout=120).read()
    if time.time() - start > 2:
        proc.terminate()
        raise SystemExit(f"el daemon tarda {time.time() - start:.1f} s por petición (¿la GPU está llena y pasó a CPU?): "
                         "el hook corta a los 3 s y la condición «con» no inyectaría nada")
    return proc


def workspace(env, condition, task):
    """Copia limpia del proyecto, con su .claude, un commit inicial y la preparación de la tarea."""
    work = Path(tempfile.mkdtemp(prefix="ledger-"))
    shutil.copytree(HERE / "project", work, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(env / "skills", work / ".claude" / "skills")
    shutil.copytree(env / "agents", work / ".claude" / "agents")
    if condition == "sin":  # Con plugin, las rules viven en la librería y entran solo si se eligen.
        shutil.copytree(env / "rules", work / ".claude" / "rules")
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    for key, value in (("user.name", "bench"), ("user.email", "bench@example.com")):  # Por si no hay identidad global.
        subprocess.run(["git", "config", key, value], cwd=work, check=True)
    (work / ".git" / "info" / "exclude").write_text(".claude/\n")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=work, check=True)
    if task.get("setup"):
        subprocess.run(task["setup"], shell=True, cwd=work, check=True)
    return work


def run_one(env, conds, condition, task, rep):
    args, extra = conds[condition]
    work = workspace(env, condition, task)
    cmd = ["claude", "-p", task["prompt"], "--output-format", "json", "--model", MODEL, "--no-session-persistence",
           "--dangerously-skip-permissions", "--max-turns", str(MAX_TURNS), *args]
    start = time.time()
    try:
        proc = subprocess.run(cmd, cwd=work, env={**os.environ, **extra}, capture_output=True, text=True, timeout=TIMEOUT)
        reply = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, ValueError) as exc:
        reply = {"is_error": True, "result": f"{type(exc).__name__}"}
    check = subprocess.run([sys.executable, "-c", CHECK_PRELUDE + task["check"]], cwd=work, capture_output=True, text=True)
    usage = reply.get("modelUsage") or {}
    shutil.rmtree(work, ignore_errors=True)
    if not usage:  # Sin respuesta del modelo (cuota agotada, red): no se apunta y se repite al retomar.
        return None
    return {"model": MODEL, "condition": condition, "task": task["id"], "rep": rep,
            "passed": check.returncode == 0 and not reply.get("is_error"),
            "cost": reply.get("total_cost_usd"), "turns": reply.get("num_turns"), "seconds": round(time.time() - start),
            "input": sum(m.get("inputTokens", 0) for m in usage.values()),
            "cache_read": sum(m.get("cacheReadInputTokens", 0) for m in usage.values()),
            "cache_write": sum(m.get("cacheCreationInputTokens", 0) for m in usage.values()),
            "output": sum(m.get("outputTokens", 0) for m in usage.values()),
            "error": None if check.returncode == 0 else (check.stderr.strip().splitlines() or [reply.get("result", "?")])[-1][:200]}


def rows():
    return [r for r in map(json.loads, RESULTS.read_text().splitlines()) if r["model"] == MODEL] if RESULTS.exists() else []


def report():
    done = rows()
    total = lambda r: r["input"] + r["cache_read"] + r["cache_write"] + r["output"]
    mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")
    print(f"{len(done)} ejecuciones, modelo {MODEL}\n")
    print("| claude-decide | Tokens por tarea | Tokens por turno | Contexto fijo («ok») | Coste por tarea | Resueltas |")
    print("|---|---|---|---|---|---|")
    for condition in ("sin", "con"):
        work = [r for r in done if r["condition"] == condition and r["task"] != "ok"]
        ok = [r for r in done if r["condition"] == condition and r["task"] == "ok"]
        print(f"| {condition} | {mean([total(r) for r in work]):,.0f} | {mean([total(r) / (r['turns'] or 1) for r in work]):,.0f} "
              f"| {mean([total(r) for r in ok]):,.0f} | {mean([r['cost'] or 0 for r in work]):.3f} US$ "
              f"| {sum(r['passed'] for r in work)}/{len(work)} |")


def main():
    if "--report" in sys.argv:
        return report()
    only = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")), None)
    reps = int(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--reps=")), REPS))
    tasks = [t for t in TASKS if not only or t["id"] in only.split(",")]
    env = environment()
    conds = conditions(env)
    done = {(r["condition"], r["task"], r["rep"]) for r in rows()}
    jobs = [(c, t, rep) for rep in range(reps) for t in tasks for c in ("sin", "con") if (c, t["id"], rep) not in done]
    print(f"{len(jobs)} ejecuciones pendientes con {MODEL}", flush=True)
    if not jobs:
        return report()
    daemon = start_daemon()
    try:
        with ThreadPoolExecutor(PARALLEL) as pool, RESULTS.open("a") as out:
            for (condition, task, rep), future in zip(jobs, [pool.submit(run_one, env, conds, *job) for job in jobs]):
                row = future.result()
                if row is None:
                    print(f"{condition} {task['id']:15} #{rep} sin respuesta del modelo: se repetirá", flush=True)
                    continue
                out.write(json.dumps(row) + "\n")
                out.flush()
                print(f"{row['condition']} {row['task']:15} #{row['rep']} {'ok ' if row['passed'] else 'MAL'} "
                      f"{row['cost'] or 0:.3f} US$ {row['turns']} turnos", flush=True)
    finally:
        daemon.terminate()
        daemon.wait()
    report()


if __name__ == "__main__":
    main()
