"""Herramientas de los servidores MCP configurados, pedidas con `tools/list` y guardadas en caché.

    python mcp_catalog.py [cwd]     # refresca la caché y lista lo que encontró

Puntuar no conecta con nadie: el catálogo lee solo la caché (mcp_tools.json en la carpeta de datos).
El daemon la refresca en segundo plano al arrancar si tiene más de un día.
Los conectores de claude.ai y los integrados (chrome) no están en la configuración local: no entran.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# Con CLAUDE_CONFIG_DIR, Claude Code guarda ahí también su .claude.json.
CLAUDE_JSON = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json" if os.environ.get("CLAUDE_CONFIG_DIR") else Path.home() / ".claude.json"
TIMEOUT = 10  # segundos por servidor
TTL = 24 * 3600
PROTOCOL = "2025-06-18"
INIT = {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "claude-decide", "version": "0.2"}}


def servers(cwd, claude_json=CLAUDE_JSON):
    """Servidores activos para este directorio: los del usuario, los locales del proyecto y su .mcp.json."""
    cwd = Path(cwd).resolve()
    try:
        config = json.loads(claude_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
    project = config.get("projects", {}).get(str(cwd), {})
    found = dict(config.get("mcpServers", {}))
    try:  # ponytail: se incluye aunque el usuario no haya aprobado el .mcp.json; los MCP de plugins no entran.
        found.update(json.loads((cwd / ".mcp.json").read_text(encoding="utf-8")).get("mcpServers", {}))
    except (OSError, ValueError):
        pass
    found.update(project.get("mcpServers", {}))
    for name in project.get("disabledMcpServers", []):
        found.pop(name, None)
    return found


def sse_messages(text):
    """Mensajes JSON de una respuesta: JSON directo o eventos SSE (`data: {...}`)."""
    text = text.strip()
    if text.startswith("{"):
        return [json.loads(text)]
    return [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:") and line[5:].strip()]


def list_http(config):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               **config.get("headers", {})}

    def call(message):
        request = urllib.request.Request(config["url"], json.dumps(message).encode(), headers)
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if response.headers.get("mcp-session-id"):
                headers["mcp-session-id"] = response.headers["mcp-session-id"]
            body = response.read().decode("utf-8", errors="replace")
        return next((m for m in sse_messages(body) if m.get("id") == message.get("id")), {}) if body else {}

    call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INIT})
    call({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return paginate(lambda n, params: call({"jsonrpc": "2.0", "id": n, "method": "tools/list", "params": params}))


def list_stdio(config):
    env = {**os.environ, **config.get("env", {})}
    proc = subprocess.Popen([config["command"], *config.get("args", [])], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True)
    deadline = time.monotonic() + TIMEOUT

    def send(message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(l) for l in proc.stdout], daemon=True).start()

    def reply(n):
        while True:
            try:
                line = lines.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError("el servidor no respondió") from None
            if line.strip().startswith("{") and json.loads(line).get("id") == n:
                return json.loads(line)

    def call(message):
        send(message)
        return reply(message["id"])

    try:
        call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INIT})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return paginate(lambda n, params: call({"jsonrpc": "2.0", "id": n, "method": "tools/list", "params": params}))
    finally:
        proc.kill()


def paginate(request):
    tools, params, n = [], {}, 2
    while True:
        result = request(n, params).get("result", {})
        tools += [{"name": t["name"], "description": t.get("description", "")} for t in result.get("tools", [])]
        if not result.get("nextCursor"):
            return tools
        params, n = {"cursor": result["nextCursor"]}, n + 1


def fetch(config):
    return list_http(config) if config.get("type") in ("http", "sse") or "url" in config else list_stdio(config)


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def refresh(cache_path, configs, force=False):
    """Pide `tools/list` a los servidores sin caché o con caché vieja; un fallo queda apuntado."""
    cache = load(cache_path)
    for name, config in configs.items():
        if not force and time.time() - cache.get(name, {}).get("ts", 0) < TTL:
            continue
        try:
            cache[name] = {"ts": time.time(), "tools": fetch(config)}
        except (OSError, ValueError, KeyError, TimeoutError, urllib.error.URLError) as exc:
            cache[name] = {"ts": time.time(), "tools": [], "error": f"{type(exc).__name__}: {exc}"}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return cache


def cached_tools(cache_path, cwd, claude_json=CLAUDE_JSON):
    """(servidor, tool) de los servidores activos en este directorio, según la caché."""
    cache = load(cache_path)
    return [(name, tool) for name in servers(cwd, claude_json) for tool in cache.get(name, {}).get("tools", [])]


if __name__ == "__main__":
    from catalog import DATA
    cwd = sys.argv[1] if len(sys.argv) > 1 else Path.cwd()
    for name, entry in refresh(DATA / "mcp_tools.json", servers(cwd), force=True).items():
        print(f"{name}: {len(entry['tools'])} tools {entry.get('error', '')}")
        for tool in entry["tools"]:
            print(f"  mcp__{name}__{tool['name']:35} {tool['description'][:60]}")
