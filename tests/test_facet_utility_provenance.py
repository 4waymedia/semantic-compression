"""UTILITY reads the build's own non-lexical provenance — and reads it VERBATIM.

Two bugs, one file:

    1. `assign_facet` knew 27 structural surfaces (`STRUCTURAL_IDS`) while the builder
       emitted a 14,410-surface provenance list next to it. 14,394 CSS/HTML/JS surfaces
       shipped as CONTENT in elo-browser-v04; UTILITY measured 99.85% CONTENT,
       effective cardinality 1.012, and ELO-Browser ranked salience with it.

    2. My first fix read that file with `.strip()`. A space is PART of an ELO surface,
       so stripping collapsed 14,410 surfaces to 7,442 and merged `'return '`
       (web-structure only) into `'return'` (4,732 transcript occurrences). 93 ordinary
       words would have shipped as STRUCTURAL -- `return`, `let`, `new`, `progress`,
       `function`, `border`, `block`. The fix measured BETTER than the bug on every
       summary statistic and was wrong on the words people actually use; only a full
       re-derivation against the shipped channel caught it.

Case 2 is the one that matters. Case 1 is a missing input; case 2 is a fix that looks
right, measures better, and is wrong on the words people actually use.

    python semantic_compression/tests/test_facet_utility_provenance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SC = ROOT / "semantic_compression"
sys.path.insert(0, str(SC))

import config as cfg                                              # noqa: E402
from facets import assign_facet, load_nonlexical, load_overrides   # noqa: E402

OVERRIDES = SC / "data" / "facet_overrides.tsv"
PROVENANCE = SC / "data" / "nonlexical_terms_elo-browser-v04.txt"


def _utility(surface, ov, nl):
    return cfg.UTILITY_NAME[(assign_facet(surface, ov, nl)[2] & 0xC0) >> 6]


def run() -> int:
    fails = 0

    def check(label, got, want):
        nonlocal fails
        ok = got == want
        fails += (not ok)
        print(f"  {'ok ' if ok else 'FAIL'} {label:52}{got!r}"
              f"{'' if ok else f'   want {want!r}'}")

    ov = load_overrides(str(OVERRIDES))

    print("load_nonlexical() reads surfaces verbatim:")
    if not PROVENANCE.exists():
        print(f"  SKIP provenance file absent: {PROVENANCE}")
        return 0
    nl = load_nonlexical(PROVENANCE)
    check("surfaces are NOT stripped", len(nl), 14410)
    check("...and stripping collapses the set", len({s.strip() for s in nl if s.strip()}),
          7442)
    check("space-bearing surfaces present", " progress" in nl, True)
    check("'return ' is web-structure-only", "return " in nl, True)
    check("'return' (bare) is NOT", "return" in nl, False)

    print("\nthe fix: non-lexical surfaces stop reading as CONTENT")
    for surf in (" -mb-0.5", "!important", " progress", "return "):
        check(f"utility({surf!r})", _utility(surf, ov, nl), "STRUCTURAL")

    print("\nTHE REGRESSION -- ordinary words must be untouched by provenance")
    for surf in ("return", "let", "new", "progress", "function", "dog", "freedom"):
        check(f"utility({surf!r})", _utility(surf, ov, nl), "CONTENT")

    print("\nprecedence: an AUTHORED list outranks corpus provenance")
    # 'from ' is in the provenance file AND is a closed-class function word. Ranked the
    # other way it became STRUCTURAL -- right for `import x from `, wrong for English.
    check("'from ' stays FUNCTION", _utility("from ", ov, nl), "FUNCTION")
    check("'the' stays FUNCTION", _utility("the", ov, nl), "FUNCTION")
    check("'um' stays FILLER", _utility("um", ov, nl), "FILLER")

    print("\nnothing changes when provenance is absent (the shipped v04 behaviour)")
    for surf in (" -mb-0.5", "!important"):
        check(f"utility({surf!r}) with nonlexical=None",
              _utility(surf, ov, None), "CONTENT")

    print("\nthe utility bits are the ONLY thing provenance moves, for a given bucket:")
    # 40 records do change bucket, and every one is the abstract-noun tie-breaker
    # correctly declining to promote a CSS class to CONCEPT. Asserted on one case.
    b_off = assign_facet(" absolute", ov, None)[0]
    b_on = assign_facet(" absolute", ov, nl)[0]
    check("' absolute' CONCEPT -> TOPIC (not promoted once non-CONTENT)",
          (cfg.BUCKET_NAME[b_off], cfg.BUCKET_NAME[b_on]), ("CONCEPT", "TOPIC"))
    check("'absolute' (the word) is unaffected",
          cfg.BUCKET_NAME[assign_facet("absolute", ov, nl)[0]],
          cfg.BUCKET_NAME[assign_facet("absolute", ov, None)[0]])

    print(f"\n{'FAILURES: %d' % fails if fails else 'all provenance cases pass'}")
    return 1 if fails else 0


def test_facet_utility_provenance():
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
