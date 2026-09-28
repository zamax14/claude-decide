"""Tokens que gasta Claude Code con y sin claude-decide, resolviendo tareas reales sobre un proyecto de ejemplo.

    python bench/tokens/run.py                 # todo, retomando lo que ya esté en results.jsonl
    python bench/tokens/run.py --only=ok --reps=1   # algunas tareas (separadas por comas) y repeticiones
    python bench/tokens/run.py --report        # solo la tabla

Dos escenarios, cada uno con y sin el plugin:
- `setup`: la configuración real de ~/.claude (skills, plugins, rules, MCP). Sin plugin = el plugin
  desactivado y las skills recortadas vueltas a «on», con --settings; nada de ~/.claude se toca.
- `saturado`: solo el proyecto, con ~100 skills, ~50 agentes y ~15 rules públicas en su .claude/, como
  quien instala todo (--setting-sources project,local: nada del usuario). Con plugin, las skills pasan a
  «solo nombre» y las rules a la librería, como haría `library.py apply` y `manage`.

Cada ejecución es un `claude -p` sobre una copia limpia de `project/`, con un daemon propio en otro puerto
con el modelo de CLAUDE_DECIDE_MODEL. Después, la comprobación de la tarea dice si quedó resuelta.
"""
import json
import os
import random
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
MODEL = os.environ.get("BENCH_MODEL", "claude-sonnet-5")
REPS, PARALLEL, MAX_TURNS, TIMEOUT = 3, 3, 30, 900
PYTHON = os.environ.get("CLAUDE_DECIDE_PYTHON", sys.executable)  # Con laya y torch, para los daemons.
N_SKILLS, N_AGENTS, N_RULES = 100, 50, 15
SOURCES = {  # Repos con licencia permisiva, clonados en .cache (git clone --depth 1).
    "agents": "https://github.com/wshobson/agents.git",                             # MIT
    "claude-code-templates": "https://github.com/davila7/claude-code-templates.git",  # MIT
    "awesome-cursorrules": "https://github.com/PatrickJS/awesome-cursorrules.git",    # CC0
}
PORTS = {"setup": 7718, "saturado": 7719}

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


def saturate():
    """.cache/saturated/{skills,agents,rules}: lo mismo en cada ejecución (semilla fija)."""
    out = CACHE / "saturated"
    if out.exists():
        return out
    for name, url in SOURCES.items():
        if not (CACHE / name).exists():
            subprocess.run(["git", "clone", "-q", "--depth", "1", url, str(CACHE / name)], check=True)
    rng = random.Random(0)
    skills = {p.parent.name: p.parent for repo in ("agents", "claude-code-templates")
              for p in sorted((CACHE / repo).rglob("SKILL.md"))
              if frontmatter_description(p.read_text(encoding="utf-8", errors="replace"))}
    agents = {p.stem: p for repo in ("agents", "claude-code-templates") for p in sorted((CACHE / repo).rglob("agents/*.md"))
              if frontmatter_description(p.read_text(encoding="utf-8", errors="replace"))}
    rules = sorted((CACHE / "awesome-cursorrules").rglob("*.mdc"))
    for name in rng.sample(sorted(skills), N_SKILLS):
        shutil.copytree(skills[name], out / "skills" / name)
    (out / "agents").mkdir(parents=True)
    for name in rng.sample(sorted(agents), N_AGENTS):
        shutil.copy(agents[name], out / "agents" / f"{name}.md")
    (out / "rules").mkdir()
    for path in rng.sample(rules, N_RULES):
        body = path.read_text(encoding="utf-8", errors="replace")
        body = body.split("---", 2)[2] if body.startswith("---") else body  # Sin los globs de Cursor: siempre activas.
        (out / "rules" / f"{path.parent.name}-{path.stem}.md"[-80:]).write_text(body.strip() + "\n", encoding="utf-8")
    return out


def prepare_homes(saturated):
    """Datos de cada daemon y los --settings de cada condición."""
    real = Path.home() / ".claude-decide"
    setup_data = CACHE / "setup-data"
    setup_data.mkdir(parents=True, exist_ok=True)
    for name in ("state.json", "mcp_tools.json"):
        if (real / name).exists():
            shutil.copy(real / name, setup_data / name)
    collapsed = json.loads((setup_data / "state.json").read_text()).get("skills", {}) if (setup_data / "state.json").exists() else {}
    sat_home, sat_data = CACHE / "sat-home", CACHE / "sat-data"
    if not sat_home.exists():
        shutil.copytree(saturated / "rules", sat_home / "library" / "rules")
    sat_data.mkdir(exist_ok=True)
    skills = sorted(p.name for p in (saturated / "skills").iterdir())
    (sat_data / "state.json").write_text(json.dumps({"skills": dict.fromkeys(skills), "rules": []}))
    (sat_home / "settings.json").write_text(json.dumps({"skillOverrides": dict.fromkeys(skills, "name-only")}))
    return {
        ("setup", "sin"): ([ "--settings", json.dumps({"enabledPlugins": {"claude-decide@claude-decide": False},
                                                       "skillOverrides": dict.fromkeys(collapsed, "on")})], {}),
        ("setup", "con"): ([], {"CLAUDE_DECIDE_PORT": str(PORTS["setup"]), "CLAUDE_DECIDE_HOME": str(setup_data)}),
        ("saturado", "sin"): (["--setting-sources", "project,local", "--strict-mcp-config"], {}),
        ("saturado", "con"): (["--setting-sources", "project,local", "--strict-mcp-config", "--plugin-dir", str(ROOT),
                               "--settings", str(sat_home / "settings.json")],
                              {"CLAUDE_DECIDE_PORT": str(PORTS["saturado"]), "CLAUDE_DECIDE_HOME": str(sat_data),
                               "CLAUDE_DECIDE_MODE": "inject"}),
    }, {"setup": {"CLAUDE_DECIDE_HOME": str(setup_data)},
        "saturado": {"CLAUDE_DECIDE_HOME": str(sat_data), "CLAUDE_CONFIG_DIR": str(sat_home)}}


def start_daemon(scenario, extra):
    """Un daemon por escenario y uno a la vez: dos más el tuyo no caben en una GPU de 6 GB y pasan a CPU,
    donde tardan más que el límite del hook y no inyectan nada."""
    port = PORTS[scenario]
    env = {**os.environ, **extra, "CLAUDE_DECIDE_PORT": str(port)}
    log = open(CACHE / f"daemon-{scenario}.log", "a")
    proc = subprocess.Popen([PYTHON, str(ROOT / "daemon.py")], cwd=ROOT, env=env, stdout=log, stderr=log)
    for _ in range(180):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            break
        except OSError:
            time.sleep(1)
    else:
        raise SystemExit(f"el daemon de {scenario} no arrancó: mira {CACHE}/daemon-{scenario}.log")
    # La primera petición arma el catálogo y calienta la GPU: si la hiciera el hook, pasaría de su límite.
    warm = json.dumps({"event": "UserPromptSubmit", "prompt": "hola", "cwd": str(HERE / "project")}).encode()
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/score", warm), timeout=120).read()
    return proc


def workspace(saturated, scenario, condition, task):
    """Copia limpia del proyecto, con un commit inicial, y el .claude saturado si toca."""
    work = Path(tempfile.mkdtemp(prefix="ledger-"))
    shutil.copytree(HERE / "project", work, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    if scenario == "saturado":
        shutil.copytree(saturated / "skills", work / ".claude" / "skills")
        shutil.copytree(saturated / "agents", work / ".claude" / "agents")
        if condition == "sin":  # Con plugin, las rules viven en la librería y entran solo si se eligen.
            shutil.copytree(saturated / "rules", work / ".claude" / "rules")
    git = ["git", "-c", "user.name=bench", "-c", "user.email=bench@example.com"]
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    (work / ".git" / "info" / "exclude").write_text(".claude/\n")
    subprocess.run(git + ["add", "-A"], cwd=work, check=True)
    subprocess.run(git + ["commit", "-qm", "initial"], cwd=work, check=True)
    if task.get("setup"):
        subprocess.run(task["setup"], shell=True, cwd=work, check=True)
    return work


def run_one(saturated, conditions, scenario, condition, task, rep):
    args, env = conditions[(scenario, condition)]
    work = workspace(saturated, scenario, condition, task)
    cmd = ["claude", "-p", task["prompt"], "--output-format", "json", "--model", MODEL, "--no-session-persistence",
           "--dangerously-skip-permissions", "--max-turns", str(MAX_TURNS), *args]
    start = time.time()
    try:
        proc = subprocess.run(cmd, cwd=work, env={**os.environ, **env}, capture_output=True, text=True, timeout=TIMEOUT)
        reply = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, ValueError) as exc:
        reply = {"is_error": True, "result": f"{type(exc).__name__}"}
    check = subprocess.run([sys.executable, "-c", CHECK_PRELUDE + task["check"]], cwd=work, capture_output=True, text=True)
    usage = reply.get("modelUsage") or {}
    row = {"scenario": scenario, "condition": condition, "task": task["id"], "rep": rep,
           "passed": check.returncode == 0 and not reply.get("is_error"),
           "cost": reply.get("total_cost_usd"), "turns": reply.get("num_turns"), "seconds": round(time.time() - start),
           "input": sum(m.get("inputTokens", 0) for m in usage.values()),
           "cache_read": sum(m.get("cacheReadInputTokens", 0) for m in usage.values()),
           "cache_write": sum(m.get("cacheCreationInputTokens", 0) for m in usage.values()),
           "output": sum(m.get("outputTokens", 0) for m in usage.values()),
           "error": None if check.returncode == 0 else (check.stderr.strip().splitlines() or ["?"])[-1][:200]}
    shutil.rmtree(work, ignore_errors=True)
    return row


def report():
    rows = [json.loads(l) for l in RESULTS.read_text().splitlines()] if RESULTS.exists() else []
    print(f"{len(rows)} ejecuciones, modelo {MODEL}\n")
    print("| Escenario | claude-decide | Tokens por tarea (entrada + caché + salida) | Solo el contexto fijo («ok») | Coste por tarea | Resueltas |")
    print("|---|---|---|---|---|---|")
    for scenario in PORTS:
        for condition in ("sin", "con"):
            sel = [r for r in rows if r["scenario"] == scenario and r["condition"] == condition]
            work = [r for r in sel if r["task"] != "ok"]
            ok = [r for r in sel if r["task"] == "ok"]
            if not sel:
                continue
            total = lambda r: r["input"] + r["cache_read"] + r["cache_write"] + r["output"]
            mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")
            print(f"| {scenario} | {condition} | {mean([total(r) for r in work]):,.0f} | {mean([total(r) for r in ok]):,.0f} "
                  f"| {mean([r['cost'] or 0 for r in work]):.3f} US$ | {sum(r['passed'] for r in work)}/{len(work)} |")


def main():
    if "--report" in sys.argv:
        return report()
    only = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")), None)
    reps = int(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--reps=")), REPS))
    tasks = [t for t in TASKS if not only or t["id"] in only.split(",")]
    saturated = saturate()
    conditions, daemon_env = prepare_homes(saturated)
    done = {(r["scenario"], r["condition"], r["task"], r["rep"]) for r in map(json.loads, RESULTS.open())} if RESULTS.exists() else set()
    jobs = [(s, c, t, rep) for rep in range(reps) for t in tasks for s in PORTS for c in ("sin", "con")
            if (s, c, t["id"], rep) not in done]
    print(f"{len(jobs)} ejecuciones pendientes", flush=True)
    for scenario in PORTS:
        todo = [job for job in jobs if job[0] == scenario]
        if not todo:
            continue
        daemon = start_daemon(scenario, daemon_env[scenario])
        try:
            with ThreadPoolExecutor(PARALLEL) as pool, RESULTS.open("a") as out:
                futures = [pool.submit(run_one, saturated, conditions, *job) for job in todo]
                for future in futures:
                    row = future.result()
                    out.write(json.dumps(row) + "\n")
                    out.flush()
                    print(f"{row['scenario']:8} {row['condition']} {row['task']:15} #{row['rep']} "
                          f"{'ok ' if row['passed'] else 'MAL'} {row['cost'] or 0:.3f} US$ {row['turns']} turnos", flush=True)
        finally:
            daemon.terminate()
            daemon.wait()
    report()


if __name__ == "__main__":
    main()
