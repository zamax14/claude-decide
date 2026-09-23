import math
import unittest

import scorer

ITEMS = [{"id": f"skill:{n}", "tipo": "skill", "nombre": n, "descripcion": d, "ruta": ""}
         for n, d in (("commit", "git commits"), ("docs", "library docs"), ("todo", "says yes to everything"))]


class FakeModel:
    """«Sí» si alguna palabra de la descripción está en la petición; «todo» siempre dice que sí."""
    name = "fake"

    def __init__(self):
        self.calls = 0

    def predict(self, state, questions):
        self.calls += 1
        answers = {}
        for qid, q in questions.items():
            description = q["instructions"].split(": ", 1)[1]
            hit = any(word in state for word in description.split())
            answers[qid] = {"noul": .95 if qid == "skill:todo" else (.8 if hit else .1)}
        return {"answers": answers}


class ScorerTest(unittest.TestCase):
    def test_prior_removes_the_item_that_always_says_yes(self):
        model, prior = FakeModel(), {}
        self.assertTrue(scorer.calibrate(model, ITEMS, prior, path=None))
        ranked = scorer.rank(model, ITEMS, "haz un commit con git", prior)
        self.assertEqual(ranked[0]["id"], "skill:commit")
        self.assertAlmostEqual(next(r for r in ranked if r["id"] == "skill:todo")["score"], .5, places=2)
        self.assertEqual([r["id"] for r in scorer.select(ranked, .6, 5)], ["skill:commit"])

    def test_calibration_is_cached_until_the_description_changes(self):
        model, prior = FakeModel(), {}
        scorer.calibrate(model, ITEMS, prior, path=None)
        self.assertFalse(scorer.calibrate(model, ITEMS, prior, path=None))
        changed = [dict(ITEMS[0], descripcion="otra cosa")]
        self.assertTrue(scorer.calibrate(model, changed, prior, path=None))

    def test_batches_every_item_once(self):
        model, items = FakeModel(), [dict(ITEMS[0], id=f"skill:{n}") for n in range(scorer.BATCH + 1)]
        self.assertEqual(len(scorer.raw_scores(model, items, "x")), scorer.BATCH + 1)
        self.assertEqual(model.calls, 2)

    def test_logit_is_finite_at_the_edges(self):
        self.assertTrue(math.isfinite(scorer.logit(0)) and math.isfinite(scorer.logit(1)))


if __name__ == "__main__":
    unittest.main()
