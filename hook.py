"""Hook UserPromptSubmit y PostToolUse: pide al daemon el contexto elegido y se lo pasa a Claude.

Solo biblioteca estándar, para arrancar rápido con cualquier python3. Si el daemon no responde,
lo lanza en segundo plano y esta vez no inyecta nada: Claude sigue como si el plugin no estuviera.

CLAUDE_DECIDE_MODE:
- `auto` (por defecto): inyecta solo si library.py recortó algo. Sin recorte, Claude ya tiene todas
  las descripciones y, con Laya sin afinar, 4 de cada 5 sugerencias sobran (bench/run.py).
- `inject`: inyecta siempre.
- `log`: nunca inyecta; solo queda apuntado en el log del daemon.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("CLAUDE_DECIDE_HOME", Path.home() / ".claude-decide"))  # El mismo que catalog.DATA.
URL = f"http://127.0.0.1:{os.environ.get('CLAUDE_DECIDE_PORT', 7717)}/score"
TIMEOUT = 3  # segundos; el hook de Claude Code corta a los 5.


def start_daemon():
    python = os.environ.get("CLAUDE_DECIDE_PYTHON") or str(ROOT / ".venv" / "bin" / "python")
    if not Path(python).is_file():
        return
    logs = DATA / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if os.environ.get("CLAUDE_DECIDE_HF_HOME"):  # Laya ya descargada en otra caché de Hugging Face.
        env["HF_HOME"] = os.environ["CLAUDE_DECIDE_HF_HOME"]
    # Si dos hooks lo lanzan a la vez, el segundo no consigue el puerto y sale solo.
    subprocess.Popen([python, str(ROOT / "daemon.py")], cwd=ROOT, env=env, start_new_session=True,
                     stdout=open(logs / "daemon.log", "a"), stderr=subprocess.STDOUT)


def should_inject():
    mode = os.environ.get("CLAUDE_DECIDE_MODE", "auto")
    if mode != "auto":
        return mode == "inject"
    try:
        state = json.loads((DATA / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(state.get("skills") or state.get("rules"))


def main():
    event = json.load(sys.stdin)
    name = event.get("hook_event_name", "UserPromptSubmit")
    body = json.dumps({"event": name, "prompt": event.get("prompt", ""), "cwd": event.get("cwd", ""),
                       "session_id": event.get("session_id", ""), "tool_name": event.get("tool_name", ""),
                       "tool_input": event.get("tool_input", "")}).encode()
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
    if result.get("context") and should_inject():
        print(json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": result["context"]}},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
