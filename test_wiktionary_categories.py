"""
test_wiktionary_categories.py -- gate for the Wiktionary category word base.

Proves the recursive category walk collects nested terms with topic labels
(offline, from the bundled sample), and the word-base round-trips.

Run from the repo root (R-D-concepts/):
    python -m semantic_compression.test_wiktionary_categories
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from semantic_compression.wiktionary_categories import (
    AuthorityTerm, harvest_sample, write_wordbase,
)


def test_harvest_walks_tree():
    terms = harvest_sample("en:Sciences")
    by = {t.term: set(t.topics) for t in terms}
    # nested terms are reached (Genetics under Biology; Optics under Physics)
    assert "allele" in by and "en:Genetics" in by["allele"]
    assert "refraction" in by and "en:Optics" in by["refraction"]
    assert "science" in by and "en:Sciences" in by["science"]
    # every term carries at least one topic + the wiktionary source
    assert all(t.topics and t.source == "wiktionary" for t in terms)
    # reasonable breadth from the sample tree
    assert len(terms) >= 15


def test_wordbase_roundtrip():
    terms = harvest_sample("en:Sciences")
    d = tempfile.mkdtemp()
    path = Path(d) / "wb.tsv"
    write_wordbase(terms, path)
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln and not ln.startswith("#")]
    assert len(lines) == len(terms)
    term0, topics0, source0 = lines[0].split("\t")
    assert source0 == "wiktionary" and topics0


def main() -> None:
    tests = [test_harvest_walks_tree, test_wordbase_roundtrip]
    for t in tests:
        t()
        print(f"[OK] {t.__name__}")
    print(f"\n=== test_wiktionary_categories: {len(tests)}/{len(tests)} PASSED ===")


if __name__ == "__main__":
    main()
