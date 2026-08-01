"""
test_verbalizer_ops.py -- probes for label / summarize / verbalize.

Measured, not predicted (invariant #7): pure cases always run; facet-dependent
cases SKIP (never PASS) when the substrate is absent. Includes the two "beat"
probes -- label vs labelTopic, verbalize vs the quoting fallback -- and the
degradation cases (empty seeds, no shape, budget 1).

    python -m unittest tests.test_verbalizer_ops -v      (run from semantic_compression/)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # semantic_compression/

from verbalizer_ops import label, summarize, verbalize, stance_from_seed   # noqa: E402
from response import cue_read                                   # noqa: E402

_FACETS = bool(cue_read(["word"]))          # facet channel importable here?


# ------------------------------------------------------------------ label ----
class TestLabel(unittest.TestCase):

    def test_prefers_multiword_content(self):
        # a learned phrase surface beats scattered single terms (no facets needed)
        r = label([{"id": "i1", "text": "..."}],
                  {"surfaces": ["car wash", "drive", "walk"]})
        self.assertEqual(r["label"], "car wash")
        self.assertEqual(r["basis"], ["car wash"])

    def test_grounds_on_item_ids(self):
        r = label([{"id": "a", "text": "x"}, {"id": "b", "text": "y"}],
                  {"surfaces": ["system"]})
        self.assertEqual(r["grounded_on"], ["a", "b"])

    def test_label_is_1_to_4_words(self):
        r = label([{"id": "i", "text": "x"}], {"surfaces": ["the quick brown fox jumps"]})
        self.assertLessEqual(len(r["label"].split()), 4)

    @unittest.skipUnless(_FACETS, "facet channel unavailable")
    def test_beats_labeltopic_by_dropping_function_words(self):
        # labelTopic would return the most frequent term ('the'); the facet utility
        # bit marks it FUNCTION, so label picks the content word instead.
        r = label([{"id": "i", "text": "x"}], {"surfaces": ["the", "the", "the", "system"]})
        self.assertEqual(r["label"], "system")


# --------------------------------------------------------------- summarize ----
class TestSummarize(unittest.TestCase):

    def _items(self):
        return [
            {"id": "s1", "kind": "filler", "text": "well anyway"},
            {"id": "s2", "kind": "fact", "text": "the server has 128 GB of ram"},
            {"id": "s3", "kind": "intent", "text": "i want to upgrade it"},
        ]

    def test_budget_and_dropped(self):
        r = summarize(self._items(), budget=2)
        self.assertEqual(len(r["sentences"]), 2)
        self.assertEqual(r["dropped"], 1)                 # no silent truncation

    def test_salience_fact_first(self):
        r = summarize(self._items(), budget=1)
        self.assertEqual(r["sentences"][0]["grounded_on"], ["s2"])   # fact outranks filler

    def test_numbers_preserved(self):
        r = summarize([{"id": "s2", "kind": "fact", "text": "the server has 128 GB of ram"}], 1)
        self.assertIn("128", r["summary"])                # invariant #6

    def test_each_sentence_cites_its_source(self):
        r = summarize(self._items(), budget=3)
        for sent in r["sentences"]:
            self.assertEqual(len(sent["grounded_on"]), 1)

    def test_budget_one_degradation(self):
        r = summarize(self._items(), budget=1)
        self.assertEqual(len(r["sentences"]), 1)
        self.assertEqual(r["dropped"], 2)


# --------------------------------------------------------------- verbalize ----
class TestVerbalize(unittest.TestCase):

    def test_gap_in_shape_definition(self):
        # no seeds, a definition-shaped ask -> names the subject, not "nothing stored"
        r = verbalize({"shape": {"shape": "definition", "subject": "car wash"}, "seeds": []})
        self.assertIn("car wash", r["text"])
        self.assertEqual(r["grounded_on"], [])

    def test_grounded_modal_choice_beats_quoting(self):
        fact = "You are 100 feet from the car wash"
        r = verbalize({
            "shape": {"shape": "modal_choice", "options": ["drive", "walk"], "subject": "car wash"},
            "seeds": [{"id": "s1", "kind": "fact", "text": fact, "stance": "told"}],
        })
        # names both options + the open part -- not the bare quote
        self.assertIn("drive", r["text"])
        self.assertIn("walk", r["text"])
        self.assertIn("What matters", r["text"])
        self.assertNotEqual(r["text"], f'From what you have told me: "{fact}."')
        self.assertEqual(r["grounded_on"], ["s1"])
        self.assertEqual(r["stance_marks"][0]["stance"], "told")

    def test_definition_mention_not_definition(self):
        r = verbalize({
            "shape": {"shape": "definition", "subject": "car wash"},
            "seeds": [{"id": "s1", "text": "i want to wash the car", "stance": "told"}],
        })
        self.assertIn("mentions car wash", r["text"])     # invariant #4

    def test_attribute_answer_via_query(self):
        r = verbalize({
            "query": "whats your name?",
            "seeds": [{"id": "s1", "text": "Your name is Elo"}],
        })
        self.assertEqual(r["text"], "My name is Elo.")
        self.assertEqual(r["grounded_on"], ["s1"])

    def test_social_act_via_query(self):
        r = verbalize({"query": "hello", "seeds": []})
        self.assertIn("Hello", r["text"])
        self.assertEqual(r["grounded_on"], [])

    def test_inferred_stance_uses_inferred_voice(self):
        r = verbalize({
            "shape": {"shape": "statement"},
            "seeds": [{"id": "r1", "text": "the deploy contributes_to the outage", "stance": "inferred"}],
        })
        self.assertTrue(r["text"].startswith("That would suggest"))
        self.assertEqual(r["stance_marks"][0]["stance"], "inferred")

    def test_no_shape_defaults_to_statement(self):
        r = verbalize({"seeds": [{"id": "s1", "text": "the sky is blue", "stance": "told"}]})
        self.assertIn("told me", r["text"])
        self.assertEqual(r["grounded_on"], ["s1"])

    def test_empty_seeds_no_shape(self):
        r = verbalize({"seeds": []})
        self.assertTrue(r["text"])                        # a legible gap, not a crash
        self.assertEqual(r["grounded_on"], [])


class TestBasis(unittest.TestCase):
    """A1: verbalize reports which rung fired -- the gateway keys side effects on it."""

    def test_basis_social(self):
        self.assertEqual(verbalize({"query": "hello", "seeds": []})["basis"], "social")

    def test_basis_attribute(self):
        r = verbalize({"query": "whats your name?",
                       "seeds": [{"id": "s1", "text": "Your name is Elo"}]})
        self.assertEqual(r["basis"], "attribute")

    def test_basis_gap(self):
        self.assertEqual(verbalize({"seeds": []})["basis"], "gap")   # -> _wonder_on_gap

    def test_basis_grounded(self):
        r = verbalize({"shape": {"shape": "modal_choice", "options": ["drive", "walk"]},
                       "seeds": [{"id": "s1", "text": "You are 100 feet from the car wash",
                                  "stance": "told"}]})
        self.assertEqual(r["basis"], "grounded")

    def test_basis_fallback_when_shape_has_no_form(self):
        # statement shape -> no specific form -> the seed is quoted -> 'fallback'
        r = verbalize({"shape": {"shape": "statement"},
                       "seeds": [{"id": "s1", "text": "the sky is blue", "stance": "told"}]})
        self.assertEqual(r["basis"], "fallback")


class TestStanceMapping(unittest.TestCase):
    """A3: one spec'd mapping from stored seed fields -> stance."""

    def test_told_is_default(self):
        self.assertEqual(stance_from_seed(claim_type="factual"), "told")

    def test_reasoning_is_inferred(self):
        self.assertEqual(stance_from_seed(from_reasoning=True), "inferred")

    def test_low_certainty_is_speculation(self):
        self.assertEqual(stance_from_seed(certainty=0.2), "speculation")

    def test_speculative_memory_type(self):
        self.assertEqual(stance_from_seed(memory_type="speculation"), "speculation")

    def test_speculation_voice_in_verbalize(self):
        r = verbalize({"shape": {"shape": "statement"},
                       "seeds": [{"id": "s1", "text": "it might rain", "stance": "speculation"}]})
        self.assertTrue(r["text"].startswith("One possibility"))
        self.assertEqual(r["stance_marks"][0]["stance"], "speculation")


class TestChain(unittest.TestCase):
    """B3: verbalize speaks a licensed inference chain in inferred voice."""

    CHAIN = [
        {"src": "washing car matters", "rel": "CO_PRESENCE", "dst": "car present",
         "rule": "co_presence", "ceiling": 0.65, "stance": "told",
         "text": "washing the car matters"},
        {"src": "driving", "rel": "ENABLES", "dst": "car present",
         "rule": "enables", "ceiling": 0.65, "stance": "told",
         "text": "Driving takes the car with you"},
        {"src": "walking", "rel": "PREVENTS", "dst": "car present",
         "rule": "blocks", "ceiling": 0.75, "stance": "told",
         "text": "Walking leaves it behind"},
    ]

    def _recommend(self, **over):
        cv = {"kind": "recommend", "option": "drive",
              "verdicts": {"drive": "SATISFIES", "walk": "DEFEATS"},
              "requirement": "car present", "requirement_source": "axiom",
              "chain": self.CHAIN, "confidence": 0.65, "stance": "told", "ask": None}
        cv.update(over)
        return cv

    def test_recommend_is_inferred_and_cited(self):
        r = verbalize({"chain_verdict": self._recommend()})
        self.assertEqual(r["basis"], "inferred")            # only a chain says this
        self.assertTrue(r["text"].startswith("Drive"))
        self.assertIn("washing the car matters", r["text"])
        self.assertIn("the car must be there", r["text"])   # axiom, rendered
        self.assertIn("Walking leaves it behind", r["text"])  # loser effect
        self.assertEqual(len(r["grounded_on"]), 3)          # every step
        self.assertEqual(r["stance_marks"][0]["stance"], "told")

    def test_chain_wins_over_seed_recall(self):
        # even with a recalled seed present, the chain composes the answer
        r = verbalize({"chain_verdict": self._recommend(),
                       "seeds": [{"id": "s9", "text": "cars are red", "stance": "told"}]})
        self.assertEqual(r["basis"], "inferred")

    def test_recommend_gap_ends_on_the_question(self):
        cv = self._recommend(kind="recommend_gap",
                             verdicts={"drive": "SATISFIES", "walk": "UNKNOWN"},
                             ask="Does walk affect car present?")
        r = verbalize({"chain_verdict": cv})
        self.assertEqual(r["basis"], "inferred")
        self.assertTrue(r["text"].rstrip().endswith("?"))   # opens a capture slot

    def test_tie_and_neither_ask(self):
        tie = verbalize({"chain_verdict": self._recommend(
            kind="tie", option=None, verdicts={"drive": "SATISFIES", "cycle": "SATISFIES"})})
        self.assertTrue(tie["text"].startswith("Either works"))
        self.assertTrue(tie["text"].rstrip().endswith("?"))
        neither = verbalize({"chain_verdict": self._recommend(
            kind="neither", option=None, verdicts={"walk": "DEFEATS", "swim": "DEFEATS"})})
        self.assertTrue(neither["text"].startswith("Neither"))

    def test_perception_stance_hedges(self):
        r = verbalize({"chain_verdict": self._recommend(stance="perception")})
        self.assertTrue(r["text"].startswith("From what you've seen,"))   # John rule
        self.assertEqual(r["stance_marks"][0]["stance"], "perception")

    def test_taught_requirement_reads_differently_than_axiom(self):
        r = verbalize({"chain_verdict": self._recommend(requirement_source="taught")})
        self.assertNotIn("must be there", r["text"])         # not the axiom phrasing
        self.assertIn("you told me that needs", r["text"])

    def test_unknown_falls_through(self):
        # kind 'unknown' composes NOTHING from the chain -> the ladder floor holds
        r = verbalize({"chain_verdict": {"kind": "unknown", "chain": self.CHAIN},
                       "seeds": []})
        self.assertNotEqual(r["basis"], "inferred")
        self.assertEqual(r["basis"], "gap")


if __name__ == "__main__":
    unittest.main()
