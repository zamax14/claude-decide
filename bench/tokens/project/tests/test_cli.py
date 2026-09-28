import io
import os
import tempfile
import unittest

from ledger import cli


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LEDGER_DB"] = os.path.join(self.tmp.name, "t.db")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out = io.StringIO()
        cli.main(list(argv), out)
        return out.getvalue()

    def test_add_then_list(self):
        self.assertIn("added #1", self.run_cli("add", "12.5", "food", "lunch", "--day", "2026-09-02"))
        self.assertIn("lunch", self.run_cli("list", "--month", "2026-09"))

    def test_import_skips_income(self):
        path = os.path.join(self.tmp.name, "bank.csv")
        with open(path, "w") as f:
            f.write("date,amount,category,note\n2026-09-01,-20,food,market\n2026-09-02,1500,,salary\n")
        self.assertIn("imported 1", self.run_cli("import", path))
