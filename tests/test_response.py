"""
test_response.py -- the verbalizer response composer (System 2), pure + substrate-free.

    python -m unittest tests.test_response -v      (run from semantic_compression/)
"""
import sys
import types
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))          # semantic_compression/

from response import compose_response, Response, TEMPLATES   # noqa: E402


def seed(sid, concept, text):
    return types.SimpleNamespace(id=sid, concept_id=concept, raw_text=text,
                                 vec4d=[0.1, 0.1, 0.0, 0.3])


def contra(*pairs):
    return frozenset(frozenset(p) for p in pairs)


class TestReaction(unittest.TestCase):

    def test_contradiction(self):
        stable = seed("s1", "system", "The system is stable.")
        broken = seed("s2", "system", "The system is broken.")
        r = compose_response(broken, [stable], contra(("s1", "s2")))
        self.assertEqual(r.kind, "reaction")
        self.assertEqual(r.basis, "contradiction")
        self.assertIn("conflicts", r.text)
        self.assertIn("The system is stable", r.text)
        self.assertEqual(set(r.references), {"s1", "s2"})

    def test_agreement(self):
        stable = seed("s1", "system", "The system is stable.")
        fine = seed("s2", "system", "The system is fine.")
        r = compose_response(fine, [stable], contra())          # no contradiction
        self.assertEqual(r.basis, "agreement")
        self.assertIn("consistent", r.text)

    def test_novelty(self):
        db = seed("s9", "database", "The database is slow.")
        r = compose_response(db, [], contra())
        self.assertEqual(r.basis, "novelty")
        self.assertIn("database", r.text)
        self.assertEqual(r.references, ("s9",))


class TestAnswer(unittest.TestCase):

    def _q(self):
        return seed("q1", "system", "Is the system stable?")

    def test_conflict(self):
        stable = seed("s1", "system", "The system is stable.")
        broken = seed("s2", "system", "The system is broken.")
        r = compose_response(self._q(), [stable, broken], contra(("s1", "s2")))
        self.assertEqual(r.kind, "answer")
        self.assertEqual(r.basis, "conflict")
        self.assertIn("conflict", r.text)
        self.assertIn("stable", r.text)
        self.assertIn("broken", r.text)

    def test_recall(self):
        stable = seed("s1", "system", "The system is stable.")
        r = compose_response(self._q(), [stable], contra())
        self.assertEqual(r.basis, "recall")
        self.assertIn("From memory about system", r.text)

    def test_unknown(self):
        q = seed("q1", "database", "Is the database up?")
        r = compose_response(q, [], contra())
        self.assertEqual(r.basis, "unknown")
        self.assertIn("nothing stored", r.text)


class TestContract(unittest.TestCase):

    def test_question_detected_by_trailing_qmark(self):
        q = seed("q", "x", "what about x?")
        self.assertEqual(compose_response(q, [], contra()).kind, "answer")
        st = seed("s", "x", "x happened.")
        self.assertEqual(compose_response(st, [], contra()).kind, "reaction")

    def test_turn_excluded_from_its_own_priors(self):
        # passing the turn in priors must not make it agree/answer with itself
        st = seed("s1", "system", "The system is broken.")
        r = compose_response(st, [st], contra())
        self.assertEqual(r.basis, "novelty")

    def test_to_dict_roundtrips_fields(self):
        st = seed("s1", "system", "The system is broken.")
        d = compose_response(st, [], contra()).to_dict()
        self.assertEqual(set(d), {"text", "kind", "basis", "concept",
                                  "references", "confidence"})

    def test_confidence_matches_template(self):
        st = seed("s1", "system", "The system is broken.")
        prior = seed("s0", "system", "The system is stable.")
        r = compose_response(st, [prior], contra(("s0", "s1")))
        self.assertAlmostEqual(r.confidence, TEMPLATES["contradiction"][1])


if __name__ == "__main__":
    unittest.main()
