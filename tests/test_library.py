import json
import tempfile
import unittest
from pathlib import Path

import catalog
from library import Library

SETTINGS = {"model": "opus", "enabledPlugins": {"claude-decide@claude-decide": True},
            "skillOverrides": {"graphify": "off"}}


class LibraryTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.home, self.data = tmp / "claude", tmp / "data"
        for name in ("find-docs", "graphify"):
            (self.home / "skills" / name).mkdir(parents=True)
            (self.home / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} x\n---\n")
        (self.home / "rules").mkdir()
        (self.home / "rules" / "context7.md").write_text("Usa ctx7.\n")
        self.original = json.dumps(SETTINGS, indent=2) + "\n"
        (self.home / "settings.json").write_text(self.original)

    def settings(self):
        return json.loads((self.home / "settings.json").read_text())

    def test_apply_collapses_only_skills_without_a_user_override_and_restore_undoes_it(self):
        library = Library(self.home, self.data)
        self.assertEqual(library.collapse(library.own_skills()), ["find-docs"])
        self.assertEqual(self.settings()["skillOverrides"], {"graphify": "off", "find-docs": "name-only"})
        self.assertEqual(self.settings()["model"], "opus")
        Library(self.home, self.data).restore()  # Otra instancia: el estado sale de state.json.
        self.assertEqual((self.home / "settings.json").read_text(), self.original)

    def test_restore_respects_a_value_the_user_set_afterwards(self):
        library = Library(self.home, self.data)
        library.collapse(["find-docs"])
        settings = self.settings()
        settings["skillOverrides"]["find-docs"] = "off"
        (self.home / "settings.json").write_text(json.dumps(settings))
        library.restore()
        self.assertEqual(self.settings()["skillOverrides"]["find-docs"], "off")

    def test_managed_rule_leaves_rules_and_is_marked_in_the_catalog(self):
        library = Library(self.home, self.data)
        library.manage("rule:context7")
        self.assertFalse((self.home / "rules" / "context7.md").exists())
        items = {i["id"]: i for i in catalog.build(self.home, self.home, self.data, self.home / "none.json")}
        self.assertEqual(items["rule:context7"]["gestion"], "library")
        library.collapse(["find-docs"])
        items = {i["id"]: i for i in catalog.build(self.home, self.home, self.data, self.home / "none.json")}
        self.assertEqual(items["skill:find-docs"]["gestion"], "name-only")
        library.unmanage("rule:context7")
        self.assertTrue((self.home / "rules" / "context7.md").is_file())

    def test_plugin_skills_are_refused(self):
        with self.assertRaises(SystemExit):
            Library(self.home, self.data).manage("skill:superpowers:brainstorming")


if __name__ == "__main__":
    unittest.main()
