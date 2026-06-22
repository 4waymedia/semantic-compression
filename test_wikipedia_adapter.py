"""
test_wikipedia_adapter.py -- C3 gate: Wikipedia adapter + two-source pooling.

Proves the wiki-markup cleaner strips markup, the adapter registers + yields
clean records, and a transcripts+wikipedia pool produces per-source dispersion.

Run from the repo root (R-D-concepts/):
    python -m semantic_compression.test_wikipedia_adapter
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from semantic_compression.source_adapters import ADAPTERS, iter_records
from semantic_compression.wikipedia_adapter import clean_wikitext, WikipediaAdapter  # registers
from semantic_compression.pool_counter import SourceSpec, count_sources


def test_cleaner_strips_markup():
    raw = ("{{Short description|x}}\nThe '''cell''' is the [[basic unit|basic unit]] "
           "of [[life]].<ref>a</ref><!-- c -->\n== Structure ==\n[[File:x.svg|thumb|cap]] "
           "see [https://e.org portal].")
    out = clean_wikitext(raw)
    for residue in ("{{", "}}", "[[", "]]", "<ref", "<!--", "=="):
        assert residue not in out, f"residue {residue!r} left: {out!r}"
    assert "cell" in out and "basic unit" in out and "life" in out
    assert "Structure" in out               # heading text kept
    assert "File:" not in out               # file link dropped
    assert "portal" in out                  # ext-link text kept


def test_registered_and_scan():
    assert "wikipedia" in ADAPTERS
    assert get_license()  # license tagged
    recs = list(iter_records("wikipedia"))
    assert recs, "no wikipedia sample records"
    blob = " ".join(r.text.lower() for r in recs)
    assert "cell" in blob and "leadership" in blob
    assert all(r.source_id == "wikipedia" for r in recs)


def get_license() -> str:
    return WikipediaAdapter().license


def test_two_source_pool_dispersion():
    pool = count_sources(
        [SourceSpec("transcripts", 1.0, {"limit": 3}), SourceSpec("wikipedia", 1.0, {})],
        show=False,
    )
    # a common word should appear in BOTH sources -> dispersion 2
    assert pool.dispersion("the") == 2, f"dispersion(the)={pool.dispersion('the')}"
    # per-source counts are tracked separately
    the = pool.per_source["the"]
    assert the.get("transcripts", 0) > 0 and the.get("wikipedia", 0) > 0
    # a wiki-domain term present, attributed to wikipedia
    assert pool.per_source.get("mitochondria", {}).get("wikipedia", 0) > 0
    assert pool.merged()["the"] > 0


def main() -> None:
    tests = [test_cleaner_strips_markup, test_registered_and_scan,
             test_two_source_pool_dispersion]
    for t in tests:
        t()
        print(f"[OK] {t.__name__}")
    print(f"\n=== test_wikipedia_adapter: {len(tests)}/{len(tests)} PASSED ===")


if __name__ == "__main__":
    main()
