"""
test_verbalizer_conformance.py -- prove the ops still match the conformance file.

The Python runner in the two-runner pattern: the committed
`verbalizer_conformance.json` is the browser's contract; this asserts the reference
ops reproduce it. If the ops change, this fails until the file is regenerated
(`python gen_verbalizer_conformance.py`) -- so the certificate can never drift
silently from the code that certifies it.

    python -m unittest tests.test_verbalizer_conformance -v   (from semantic_compression/)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gen_verbalizer_conformance import run_case, CONFORMANCE_PATH   # noqa: E402
from response import cue_read                                       # noqa: E402
import json                                                          # noqa: E402

_FACETS = bool(cue_read(["word"]))


@unittest.skipUnless(CONFORMANCE_PATH.exists(),
                     "verbalizer_conformance.json not generated yet "
                     "(run: python gen_verbalizer_conformance.py)")
class TestConformance(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(CONFORMANCE_PATH.read_text(encoding="utf-8"))

    def test_every_case_reproduces(self):
        checked, skipped = 0, 0
        for i, case in enumerate(self.data["cases"]):
            if case.get("requires") == "facets" and not _FACETS:
                skipped += 1
                continue
            with self.subTest(op=case["op"], i=i):
                self.assertEqual(run_case(case), case["expected"])
            checked += 1
        self.assertGreater(checked, 0, "no conformance cases ran")

    def test_min_coverage_per_op(self):
        per = {}
        for c in self.data["cases"]:
            per[c["op"]] = per.get(c["op"], 0) + 1
        for op in ("label", "summarize", "verbalize"):
            self.assertGreaterEqual(per.get(op, 0), 10, f"{op}: <10 conformance cases")


if __name__ == "__main__":
    unittest.main()
