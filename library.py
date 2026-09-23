"""Recorta el contexto de Claude Code y lo deja como estaba.

    python library.py apply            # tus skills pasan a «solo nombre» en ~/.claude/settings.json
    python library.py restore          # deshace todo lo que hizo esta herramienta
    python library.py manage rule:x    # saca una rule de ~/.claude/rules (entra solo si se elige)
    python library.py unmanage rule:x  # la devuelve; también vale skill:x
    python library.py status

Las skills se colapsan con `skillOverrides: "name-only"`: Claude sigue viendo el nombre y puede
usarlas, y el hook le devuelve la descripción de las que elige el modelo. Claude Code no aplica
`skillOverrides` a las skills de plugins, así que esas no se tocan. Las rules no tienen modo «solo
nombre»: se mueven a ~/.claude/library/rules. Todo lo hecho queda en state.json para deshacerlo.
"""
import json
import shutil
import sys

import catalog

PLUGIN = "claude-decide@"


def load(path):
    return catalog.read_json(path)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class Library:
    def __init__(self, home=catalog.HOME, data=catalog.DATA):
        self.home, self.data = home, data
        self.settings_path, self.state_path = home / "settings.json", data / "state.json"
        self.state = {"skills": {}, "rules": [], **load(self.state_path)}

    def _settings(self):
        return load(self.settings_path)

    def _write_settings(self, settings):
        backup = self.data / "settings.json.bak"  # La primera versión que se tocó, por si acaso.
        if self.settings_path.is_file() and not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.settings_path, backup)
        save(self.settings_path, settings)

    def _save_state(self):
        save(self.state_path, self.state)

    def plugin_enabled(self):
        return any(k.startswith(PLUGIN) and on for k, on in self._settings().get("enabledPlugins", {}).items())

    def own_skills(self):
        return [i["nombre"] for i in catalog.skills(self.home / "skills")]

    def collapse(self, names):
        """Pone «solo nombre» a estas skills, salvo a las que el usuario ya les puso un override propio."""
        settings = self._settings()
        overrides = settings.setdefault("skillOverrides", {})
        done = []
        for name in names:
            if name in self.state["skills"] or overrides.get(name, "on") != "on":
                continue
            self.state["skills"][name] = overrides.get(name)  # None = no había clave.
            overrides[name] = "name-only"
            done.append(name)
        if done:
            self._write_settings(settings)
            self._save_state()
        return done

    def expand(self, names):
        settings = self._settings()
        overrides = settings.get("skillOverrides", {})
        done = []
        for name in names:
            if name not in self.state["skills"]:
                continue
            before = self.state["skills"].pop(name)
            if overrides.get(name) == "name-only":  # Si el usuario lo cambió después, manda lo suyo.
                if before is None:
                    overrides.pop(name)
                else:
                    overrides[name] = before
            done.append(name)
        if "skillOverrides" in settings and not overrides:
            del settings["skillOverrides"]
        if done:
            self._write_settings(settings)
            self._save_state()
        return done

    def move_rule(self, name, to_library):
        live, stored = self.home / "rules" / f"{name}.md", self.home / "library" / "rules" / f"{name}.md"
        source, target = (live, stored) if to_library else (stored, live)
        if not source.is_file() or target.exists():
            raise SystemExit(f"No se puede mover {source} → {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        if to_library:
            self.state["rules"].append(name)
        else:
            self.state["rules"].remove(name)
        self._save_state()

    def manage(self, item_id):
        kind, name = item_id.split(":", 1)
        if kind == "rule":
            self.move_rule(name, True)
        elif kind == "skill" and name in self.own_skills():
            self.collapse([name])
        else:
            raise SystemExit(f"{item_id}: solo se gestionan rules y skills propias (no de plugins)")

    def unmanage(self, item_id):
        kind, name = item_id.split(":", 1)
        if kind == "rule" and name in self.state["rules"]:
            self.move_rule(name, False)
        elif kind == "skill":
            self.expand([name])

    def restore(self):
        self.expand(list(self.state["skills"]))
        for name in list(self.state["rules"]):
            self.move_rule(name, False)


def main(argv):
    library = Library()
    command, *args = argv or ["status"]
    if command in ("apply", "manage") and not library.plugin_enabled() and "--force" not in args:
        sys.exit("El plugin claude-decide no está activo en ~/.claude/settings.json: sin su hook, Claude se "
                 "quedaría sin descripciones en todas las sesiones. Instálalo antes (o usa --force).")
    args = [a for a in args if a != "--force"]
    if command == "apply":
        print("Solo nombre:", ", ".join(library.collapse(library.own_skills())) or "nada nuevo")
    elif command == "restore":
        library.restore()
        print("Restaurado.")
    elif command in ("manage", "unmanage"):
        for item_id in args:
            getattr(library, command)(item_id)
    elif command != "status":
        sys.exit(__doc__)
    print(f"skills solo nombre: {sorted(library.state['skills']) or '-'}\nrules en la librería: {library.state['rules'] or '-'}")


if __name__ == "__main__":
    main(sys.argv[1:])
