"""B5 — comparative/superlative, and the `-er` misfire. Expectations declared FIRST.

FOUND (coverage diagnosis, 2026-08-29): `morph_class('faster')` returns **NOUN**. The
agentive `-er` rule (`baker`, `runner`, `teacher`) fires on comparatives. That is a WRONG
answer, not a missing one, which is why B5 goes first — a generator reading it would treat
`faster` as a thing rather than a degree.

`morph_class('biggest')` and `('strongest')` return None: superlatives are unhandled
entirely, so `biggest` (13,750 occurrences) has no class at all.

THE DISCRIMINATOR IS THE STEM, NOT THE SUFFIX. `-er` is genuinely ambiguous in English
and no suffix rule can resolve it:

    baker   = bake  + er   agentive  -> NOUN
    faster  = fast  + er   comparative -> MOD

The evidence that separates them is already in the vocabulary: an adjective admits BOTH
`-er` and `-est` on the same stem (`fast/faster/fastest`), while an agentive noun admits
neither a superlative nor, usually, a verb paradigm on the `-er` form. So the test is the
COMPARATIVE PAIR — the same shape as the verb-paradigm test, and for the same reason:
morphology alone is ambiguous, co-occurring forms are not.

This mirrors the temporality lesson exactly. `stones`/`thinks` could not be separated by
shape; neither can `baker`/`faster`. Both needed a second form to disambiguate.

    python tests/test_morph_class.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# A vocabulary stub standing in for the build's forward keys. Comparative pairs present
# for real adjectives; absent for agentive nouns.
VOCAB = {
    'fast', 'faster', 'fastest', 'big', 'bigger', 'biggest',
    'strong', 'stronger', 'strongest', 'quick', 'quicker', 'quickest',
    'bake', 'baker', 'bakers', 'baking', 'baked',
    'run', 'runner', 'runners', 'running',
    'teach', 'teacher', 'teachers', 'teaching', 'taught',
    'comput', 'computer', 'computers',
    'lead', 'leader', 'leaders', 'leading',
    # for the false-positive guard: these must NOT read as degree forms
    'interest', 'inter', 'forest', 'for', 'protest', 'prot',
    # `earn` needs its verb paradigm present, or the verb-stem exclusion cannot fire
    # and `earnest` reads as a degree pair. A fixture that omits it tests nothing.
    'earnest', 'earn', 'earner', 'earning', 'earned', 'earns',
    'modest', 'mode', 'honest', 'harvest',
    'nice', 'nicer', 'nicest',
}

# surface -> expected class from morph_class(surface, vocab)
COMPARATIVES = {'faster': 'MOD', 'bigger': 'MOD', 'stronger': 'MOD', 'quicker': 'MOD'}
SUPERLATIVES = {'fastest': 'MOD', 'biggest': 'MOD', 'strongest': 'MOD', 'quickest': 'MOD'}
AGENTIVES = {'baker': 'NOUN', 'runner': 'NOUN', 'teacher': 'NOUN',
             'computer': 'NOUN', 'leader': 'NOUN'}

# FALSE POSITIVES the first implementation produced (2026-08-29). The rule tested
# whether `stem + est` was in the vocabulary -- but for a word ENDING in -est that is
# the word itself, so the test was self-satisfying: `interest` -> stem `inter` ->
# "`interest` is in vocab" -> MOD. Measured: interest, forest, protest, earnest,
# modest all wrongly MOD.
#
# The fix is that `w` supplies ONE degree form, so the EVIDENCE must be the OTHER one:
# a -est word needs an attested comparative, a -er word needs an attested superlative.
# These stay as permanent regression cases because the bug is invisible in the happy
# path -- fastest/faster pass either way.
# `interest` expects None, not NOUN: it carries no derivational suffix at all, so the
# honest answer is "no shape evidence". My first expectation here was wrong and the code
# was right -- corrected rather than forced, since a test that encodes a wrong belief is
# worse than no test.
NOT_DEGREE = {'interest': None, 'forest': None, 'protest': None,
              'modest': None, 'honest': None, 'harvest': None}

# KNOWN FALSE POSITIVE, recorded rather than special-cased. `earnest` decomposes to
# `earn` + est, and `earner` is a real, reasonably frequent English word -- so it is a
# well-formed degree pair by every test available here. Frequency cannot separate it
# because both members are genuine words; only knowing that `earnest` is not the
# superlative of `earn` would, and that is lexical knowledge this builder does not have.
# Listed so it stays visible instead of being hidden by a hardcoded exception.
XFAIL_DEGREE = {'earnest': None}

# A frequency table for the degree floor. Values mirror the real corpus order of
# magnitude: real evidence in the hundreds, ASR noise at 1-3.
FREQ = {'fastest': 993, 'faster': 900, 'biggest': 800, 'bigger': 700,
        'strongest': 989, 'stronger': 800, 'quickest': 120, 'quicker': 110,
        'nicer': 60, 'nicest': 55, 'earner': 40, 'proter': 1, 'moder': 3}

# Must keep working -- B5 must not regress the suffix families that already fire.
UNCHANGED = {'quickly': 'MOD', 'happiness': 'NOUN', 'organize': 'VERB'}


def run() -> int:
    from wordclass_builder import morph_class
    import inspect
    takes_vocab = len(inspect.signature(morph_class).parameters) > 1

    nparams = len(inspect.signature(morph_class).parameters)

    def mc(w):
        if nparams > 2:
            return morph_class(w, VOCAB, FREQ)
        return morph_class(w, VOCAB) if takes_vocab else morph_class(w)

    fails = 0
    for label, cases in (('comparatives (-er, adjective stem)', COMPARATIVES),
                         ('superlatives (-est)', SUPERLATIVES),
                         ('agentives (-er, verb stem) -- MUST stay NOUN', AGENTIVES),
                         ('NOT degree forms -- self-satisfying-rule guard', NOT_DEGREE),
                         ('existing families -- must not regress', UNCHANGED)):
        print(f'\n  {label}')
        for w, exp in sorted(cases.items()):
            act = mc(w)
            ok = act == exp
            fails += (not ok)
            print(f"    {'ok ' if ok else 'FAIL'} {w:12}"
                  f"expected {str(exp):<6}got {act}")

    print('\n  xfail -- known false positives, visible not hidden:')
    for w, exp in sorted(XFAIL_DEGREE.items()):
        act = mc(w)
        print(f"    {'(now fixed!)' if act == exp else 'xfail'} {w:12}"
              f"expected {str(exp):<6}got {act}")

    print(f"\n{'FAILURES: %d' % fails if fails else 'all morph_class cases pass'}")
    return 1 if fails else 0


def test_morph_class():
    assert run() == 0


if __name__ == '__main__':
    raise SystemExit(run())
