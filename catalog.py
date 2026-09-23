"""Catálogo de lo que Claude Code carga en una sesión: skills, rules y agentes, con su descripción.

Lee lo mismo que Claude Code: `~/.claude`, los plugins activados en los settings y el `.claude`
del proyecto. Las skills con `disable-model-invocation: true` no entran: Claude no ve su
descripción ni puede usarlas, así que no hay nada que decidir sobre ellas.
"""
import json
import sys
from pathlib import Path

HOME = Path.home() / ".claude"
MAX_DESCRIPTION = 400  # ponytail: Laya corta la pregunta a 256 tokens; lo que pase de aquí no lo lee.


def frontmatter(text):
    """Campos `clave: valor` del bloque YAML inicial y el cuerpo. Admite `>-` y `|` de varias líneas."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    fields, key = {}, None
    for line in text[4:end].splitlines():
        if line[:1] in (" ", "\t") and key:  # Continuación de un valor de varias líneas.
            fields[key] = (fields[key] + " " + line.strip()).strip()
        elif ":" in line:
            key, value = line.split(":", 1)
            key, value = key.strip(), value.strip()
            fields[key] = "" if value in (">", ">-", "|", "|-") else value.strip("\"'")
    return fields, text[end + 4:].lstrip("-\n")


def first_paragraph(body):
    lines = [l for l in body.splitlines() if l.strip() and not l.startswith("#")]
    return " ".join(lines[:4])


def item(kind, name, description, path):
    return {"id": f"{kind}:{name}", "tipo": kind, "nombre": name,
            "descripcion": " ".join(description.split())[:MAX_DESCRIPTION], "ruta": str(path)}


def skills(folder, prefix=""):
    out = []
    for path in sorted(folder.glob("*/SKILL.md")):
        fields, _ = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if fields.get("disable-model-invocation") == "true" or not fields.get("description"):
            continue
        out.append(item("skill", prefix + fields.get("name", path.parent.name), fields["description"], path))
    return out


def agents(folder, prefix=""):
    out = []
    for path in sorted(folder.glob("*.md")):
        fields, _ = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if fields.get("description"):
            out.append(item("agent", prefix + fields.get("name", path.stem), fields["description"], path))
    return out


def rules(folder):
    out = []
    for path in sorted(folder.glob("*.md")):
        _, body = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if body.strip():
            out.append(item("rule", path.stem, first_paragraph(body), path))
    return out


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def enabled_plugins(cwd):
    """Plugins activos para este directorio → carpeta instalada. Los settings del proyecto mandan."""
    enabled = {}
    for settings in (HOME / "settings.json", cwd / ".claude" / "settings.json",
                     cwd / ".claude" / "settings.local.json"):
        enabled.update(read_json(settings).get("enabledPlugins", {}))
    installed = read_json(HOME / "plugins" / "installed_plugins.json").get("plugins", {})
    out = {}
    for key, on in enabled.items():
        for entry in installed.get(key, []):
            if on and (entry.get("scope") == "user" or Path(entry.get("projectPath", "/-")) == cwd):
                out[key.split("@")[0]] = Path(entry["installPath"])
                break
    return out


def build(cwd):
    cwd = Path(cwd).resolve()
    found = skills(HOME / "skills") + rules(HOME / "rules") + agents(HOME / "agents")
    for name, folder in enabled_plugins(cwd).items():
        # ponytail: solo las carpetas por defecto; un plugin.json con rutas propias no se sigue.
        found += skills(folder / "skills", name + ":") + agents(folder / "agents", name + ":")
    if cwd != Path.home():
        project = cwd / ".claude"
        found += skills(project / "skills") + rules(project / "rules") + agents(project / "agents")
    unique = {i["id"]: i for i in found}  # El proyecto pisa al usuario si repiten nombre, como en Claude Code.
    return list(unique.values())


if __name__ == "__main__":
    for i in build(sys.argv[1] if len(sys.argv) > 1 else Path.cwd()):
        print(f"{i['id']:55} {i['descripcion'][:70]}")
