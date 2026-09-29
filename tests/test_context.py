import tempfile
import unittest
from pathlib import Path

import mcp_catalog
from daemon import Decider, step_input
from library import Library


class KeywordModel:
    """«Sí» si la petición contiene el nombre del elemento (`commit`, `docs`, `graph`...)."""
    name = "fake"

    def predict(self, state, questions):
        answers = {}
        for qid, q in questions.items():
            name = qid.split(":")[-1].split("__")[-1]
            answers[qid] = {"noul": .9 if name.split("-")[0] in state else .2}
        return {"answers": answers}


class ContextTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.home, self.data = tmp / "claude", tmp / "data"
        skill = self.home / "skills" / "docs"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: docs\ndescription: Fetches library docs, in full.\n---\n")
        (self.home / "rules").mkdir()
        (self.home / "rules" / "commit.md").write_text("Commits en una línea.\n")
        claude_json = tmp / "claude.json"
        claude_json.write_text('{"mcpServers": {"code": {"command": "x"}}}')
        (self.data).mkdir()
        (self.data / "mcp_tools.json").write_text(
            '{"code": {"ts": 0, "tools": [{"name": "graph", "description": "Search the code graph."}]}}')
        self.decider = Decider(KeywordModel(), {}, self.home, self.data, claude_json)

    def decide(self, **event):
        return self.decider.decide({"cwd": str(self.home), "session_id": "s", **event})

    def test_injects_what_claude_lost_and_nothing_it_already_has(self):
        library = Library(self.home, self.data)
        (self.home / "settings.json").write_text("{}")
        library.collapse(["docs"])
        library.manage("rule:commit")
        text = self.decide(event="UserPromptSubmit", prompt="docs y commit")["context"]
        self.assertIn("`docs` (", text)
        self.assertIn("Fetches library docs, in full.", text)  # Descripción de una skill en «solo nombre».
        self.assertIn("Commits en una línea.", text)  # Cuerpo de una rule de la librería.
        self.assertIn("still available by name", text)

    def test_mcp_tools_come_with_the_toolsearch_hint(self):
        text = self.decide(event="UserPromptSubmit", prompt="busca en el graph")["context"]
        self.assertIn("select:mcp__code__graph", text)

    def test_post_tool_use_only_sends_what_is_new_until_the_next_prompt(self):
        self.assertEqual([r["id"] for r in self.decide(event="UserPromptSubmit", prompt="docs")["selected"]],
                         ["skill:docs"])
        step = self.decide(event="PostToolUse", tool_name="Bash", tool_input={"command": "git commit"})
        self.assertEqual([r["id"] for r in step["selected"]], ["rule:commit"])  # docs ya se mandó.
        self.assertEqual(self.decide(event="PostToolUse", tool_name="Bash", tool_input={})["context"], "")
        again = self.decide(event="UserPromptSubmit", prompt="docs")
        self.assertEqual([r["id"] for r in again["selected"]], ["skill:docs"])

    def test_step_is_the_tool_and_its_main_input(self):
        self.assertEqual(step_input({"file_path": "src/a.ts", "old_string": "x\ny"}), "src/a.ts")
        self.assertEqual(step_input({"command": "npm  test\n"}), "npm test")
        self.assertEqual(step_input(""), "")

    def test_sse_and_plain_json_replies(self):
        sse = 'event: message\ndata: {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}\n\n'
        self.assertEqual(mcp_catalog.sse_messages(sse)[0]["id"], 2)
        self.assertEqual(mcp_catalog.sse_messages('{"id": 1}'), [{"id": 1}])


if __name__ == "__main__":
    unittest.main()
