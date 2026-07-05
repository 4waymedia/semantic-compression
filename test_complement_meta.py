"""
test_complement_meta.py -- the verb-complement facet in the meta layer.

Verifies the System-1 `complement` column: derive_meta populates it for verb
surfaces (inflections included), leaves it None for non-verbs and phrases, and is
deterministic (G3-friendly -- same input, same value, so the meta_fingerprint is
reproducible). Pure-Python; no LMDB/build required.

    python test_complement_meta.py      (from semantic_compression/)
"""
from __future__ import annotations

import sys
sys.path.insert(0, ".")

from verb_complements import complement_of, VERB_COMPLEMENTS
from meta_fields import derive_meta


def test_complement_of():
    cases = {
        "seem": ["to_infinitive"], "seemed": ["to_infinitive"],
        "map": ["to_noun"], "maps": ["to_noun"], "mapped": ["to_noun"],
        "tried": ["to_infinitive"], "decided": ["to_infinitive"],
        "go": ["to_infinitive", "to_noun"],            # both, sorted
        "dog": None, "the": None, "machine": None,
    }
    for surface, want in cases.items():
        got = complement_of(surface)
        assert got == want, f"complement_of({surface!r})={got!r} want {want!r}"


def test_derive_meta_column():
    m = derive_meta("seemed")
    assert m["complement"] == ["to_infinitive"], m.get("complement")
    assert (m.get("_method") or {}).get("complement") == "lexicon"
    assert derive_meta("map")["complement"] == ["to_noun"]
    assert derive_meta("dog")["complement"] is None
    # phrases never get a complement (single-word verbs only)
    assert derive_meta("machine learning")["complement"] is None


def test_deterministic():
    # same surface -> identical row value (meta_fingerprint reproducibility)
    a = derive_meta("expected")["complement"]
    b = derive_meta("expected")["complement"]
    assert a == b == ["to_infinitive"], (a, b)


def main():
    for t in (test_complement_of, test_derive_meta_column, test_deterministic):
        t()
        print(f"[OK] {t.__name__}")
    print(f"\n=== test_complement_meta: 3/3 PASSED "
          f"({len(VERB_COMPLEMENTS)} verbs in the table) ===")


if __name__ == "__main__":
    main()
