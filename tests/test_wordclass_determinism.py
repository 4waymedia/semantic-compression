"""The plural-inheritance candidate order is STABLE across processes.

THE BUG (2026-09-10). `wordclass_builder` PASS 2 collected candidate singulars into a
`set` and `break`s on the first one that resolves. Python randomises string hashing per
process, so for any plural with more than one plausible singular already resolved, the
one it inherited from was an arbitrary draw PER RUN.

Three runs over the same corpus and the same LMDB produced three different channels:

    8dadf64e75f048b9    hand-built
    6c9d52c7a0331e94    cascade rebuild -- PUBLISHED in elo-browser-v04r2
    8d7989aa1d7c81ad    re-run, same code, same inputs

The non-absent count held at EXACTLY 274,202 in all three. That is the signature and it
is why this hid for weeks: the same plurals inherit either way, so only the class moves.
A published channel that cannot be reproduced also cannot be gated -- G2's "fingerprints
agree" is worthless against a channel that disagrees with itself.

WHY A SEPARATE SUITE. `test_plural_inheritance.py` checks WHICH class a plural gets.
This checks that the answer is the SAME ONE TWICE, which is a different property and was
true of none of the three builds above. A correctness test passes happily on a coin flip
that lands the right way.

    python semantic_compression/tests/test_wordclass_determinism.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SC))


# The candidate-order logic, lifted verbatim from wordclass_builder PASS 2. Kept in step
# with the builder by test_builder_matches_this_helper below -- a copy that can drift is
# worse than no test, so the drift is itself asserted.
def candidates(low: str) -> list[str]:
    out: list[str] = []

    def _c(w: str) -> None:
        if w not in out:
            out.append(w)

    if low.endswith('ies') and len(low) > 4:
        _c(low[:-3] + 'y')
    if low.endswith('es') and len(low) > 3:
        _c(low[:-1])
        _c(low[:-2])
    if low.endswith('s') and not low.endswith('ss') and len(low) > 2:
        _c(low[:-1])
    return out


# Plurals whose candidate set has >1 member -- the only ones the bug could affect.
AMBIGUOUS = {
    "roses":   ["rose", "ros"],
    "fines":   ["fine", "fin"],
    "bases":   ["base", "bas"],
    # NOT ["ly", ...]: the -ies rule requires len > 4 and `lies` is 4, so `ly` is never
    # proposed. My first expectation here asserted the rule I imagined rather than the
    # one the code states.
    "lies":    ["lie", "li"],
    "parties": ["party", "partie", "parti"],
    "boxes":   ["boxe", "box"],
    "leaves":  ["leave", "leav"],
    "glasses": ["glasse", "glass"],
}


def run() -> int:
    fails = 0

    def check(label, got, want):
        nonlocal fails
        ok = got == want
        fails += (not ok)
        print(f"  {'ok ' if ok else 'FAIL'} {label:50}{got!r}"
              f"{'' if ok else f'   want {want!r}'}")

    print("candidate order is a LIST, and the order is the documented one:")
    for plural, want in AMBIGUOUS.items():
        check(f"candidates({plural!r})", candidates(plural), want)

    print("\nthe -e stem outranks the bare stem (the bug's most common case):")
    # `rose` is a word, `ros` is not. Under set order, `ros` won roughly half the time.
    for plural, first in (("roses", "rose"), ("fines", "fine"), ("bases", "base")):
        check(f"{plural!r} tries the -e stem first", candidates(plural)[0], first)

    print("\nSTABLE ACROSS PROCESSES -- the property the set could not provide:")
    # Separate interpreters with DIFFERENT hash seeds. Under the old `set`, this is the
    # assertion that fails; it is the whole point of the test.
    src = (f"import sys; sys.path.insert(0, {str(Path(__file__).parent)!r}); "
           f"from test_wordclass_determinism import candidates, AMBIGUOUS; "
           f"print([candidates(p) for p in sorted(AMBIGUOUS)])")
    outs = []
    for seed in ("0", "1", "12345", "random"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True,
                           env=env)
        if r.returncode != 0:
            print(f"  FAIL subprocess (PYTHONHASHSEED={seed}): {r.stderr.strip()[:200]}")
            fails += 1
            continue
        outs.append((seed, r.stdout.strip()))
    if outs:
        check("all hash seeds agree", len({o for _, o in outs}), 1)
        for seed, o in outs:
            print(f"       PYTHONHASHSEED={seed:<8} {o[:58]}...")

    # SOURCE CHECKS READ CODE, NOT COMMENTS.
    #
    # My first version of this section searched the whole file for `out: set = set()`
    # (which other functions legitimately contain) and searched a slice for the word
    # "break" -- which matched the COMMENT explaining that there is no break. Two
    # false failures from grepping prose as though it were code. Strip comments and
    # scope to the function under test.
    text = (SC / "wordclass_builder.py").read_text(encoding="utf-8")

    def _code(src: str) -> str:
        out = []
        for line in src.splitlines():
            s = line.split("#", 1)[0]
            if s.strip():
                out.append(s)
        return "\n".join(out)

    def _func(name: str) -> str:
        i = text.find(f"def {name}(")
        if i < 0:
            return ""
        j = text.find("\ndef ", i + 1)
        return _code(text[i:j if j > 0 else len(text)])

    print("\nthe builder's own source -- one definition, scoped to the function:")
    check("_singulars_of returns a list", "def _singulars_of(low: str) -> list:" in text,
          True)
    check("...and builds no set", "set()" in _func("_singulars_of"), False)
    check("PASS 2 calls it rather than copying it",
          "sings = _singulars_of(low)" in _code(text), True)

    # THE SECOND SOURCE, found 2026-09-12 after the first fix did NOT make the build
    # reproducible. `stems_of()` returns a set; the loop over it used to `break` on the
    # first match, and its two break paths assigned DIFFERENT confidences -- so a surface
    # with a strong and a weak stem got whichever came out first. Measured: 4 records
    # moved between CORPUS and HEURISTIC across two runs while the CLASS histogram stayed
    # identical, which is exactly why it survived the first fix.
    print("\nthe stem loop collects instead of breaking (the SECOND source):")
    # Delimited by the fix's own variables, and comment-stripped, so the assertion is
    # about executable code.
    _all = _code(text)
    _i = _all.find("_stem_strong = _stem_weak = False")
    _j = _all.find("if _stem_strong or _stem_weak:")
    _stem_block = _all[_i:_j] if (_i >= 0 and _j > _i) else ""
    check("the stem loop exists in the expected shape", bool(_stem_block), True)
    check("no break inside it", "break" in _stem_block, False)
    check("it iterates every stem", "for stem in stems_of(low):" in _stem_block, True)

    print("\nORDER-INDEPENDENCE IS THE PROPERTY, not a fixed order:")
    # A set whose iteration order varies must not change the verdict. This asserts the
    # shape of the fix -- collect-then-decide -- because that is what no future `break`
    # can silently undo.
    check("stems_of may still return a set (the loop no longer cares)",
          "def stems_of(w: str) -> set:" in text, True)

    print(f"\n{'FAILURES: %d' % fails if fails else 'all determinism cases pass'}")
    return 1 if fails else 0


def test_wordclass_determinism():
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
