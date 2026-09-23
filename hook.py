"""Hook UserPromptSubmit: pide al daemon la lista puntuada y se la pasa a Claude como contexto.

Solo biblioteca estándar, para arrancar rápido con cualquier python3. Si el daemon no responde,
lo lanza en segundo plano y esta vez no inyecta nada: Claude sigue como si el plugin no estuviera.
Por defecto solo apunta la decisión (modo `log`): con Laya sin afinar, 4 de cada 5 sugerencias
sobran (bench/run.py). CLAUDE_DECIDE_MODE=inject se la pasa a Claude.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = f"http://127.0.0.1:{os.environ.get('CLAUDE_DECIDE_PORT', 7717)}/score"
TIMEOUT = 3  # segundos; el hook de Claude Code corta a los 5.


def start_daemon():
    python = os.environ.get("CLAUDE_DECIDE_PYTHON") or str(ROOT / ".venv" / "bin" / "python")
    if not Path(python).is_file():
        return
    log = (ROOT / "logs").resolve()
    log.mkdir(exist_ok=True)
    # Si dos hooks lo lanzan a la vez, el segundo no consigue el puerto y sale solo.
    subprocess.Popen([python, str(ROOT / "daemon.py")], cwd=ROOT, start_new_session=True,
                     stdout=open(log / "daemon.log", "a"), stderr=subprocess.STDOUT)


def context(result):
    lines = [f"- {r['tipo']} `{r['nombre']}` ({r['score']:.2f})" for r in result["selected"]]
    return (f"claude-decide ({result['model']}) puntuó skills, rules y agentes para esta petición. "
            "Lo que más encaja, de mayor a menor:\n" + "\n".join(lines))


def main():
    event = json.load(sys.stdin)
    body = json.dumps({"prompt": event.get("prompt", ""), "cwd": event.get("cwd", "")}).encode()
    request = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            result = json.load(response)
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            start_daemon()
        return
    except (OSError, ValueError):  # Timeout o respuesta rota: no se inyecta nada.
        return
    if result.get("selected") and os.environ.get("CLAUDE_DECIDE_MODE", "log") == "inject":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                 "additionalContext": context(result)}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
