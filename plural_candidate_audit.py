"""How often does plural inheritance pick a candidate singular it should not?

THE QUESTION THIS ANSWERS (Paul, 2026-09-10): *"Why is `roses` being matched to `ros`?"*

It is not a linguistic match. PASS 2 of `wordclass_builder` generates candidate singulars
by blind string surgery -- drop `s`, drop `es`, `ies` -> `y` -- and then inherits from
**the first candidate that happens to exist in the vocabulary with a resolved class**.
`ros` wins whenever `ros` is a surface in the dictionary and is reached first.

Until 2026-09-10 "first" meant set-iteration order, which Python randomises per process,
so the choice varied per run. That is fixed (an explicit ordered list). **This script
measures the part that is NOT fixed:** whether the candidate chosen is the plausible one.

WHAT IT REPORTS
  * how many plurals have MORE THAN ONE candidate resolving -- the only ones where the
    choice matters at all, and the population the non-determinism could touch
  * for those, whether the chosen candidate is also the most FREQUENT one
  * the disagreements, so a human can see whether they look wrong

WHAT IT DOES NOT DO. It does not change the rule. Frequency is evidence about which stem
is a real word, not proof; and `wordclass_builder` line 882 is right that ranking weak
signals invents a measurement. Measure first, decide after, and decide with the numbers
visible.

    python plural_candidate_audit.py --db db/builds/elo-browser-v04/dictionary.lmdb
"""
from __future__ import annotations

import argparse
from pathlib import Path

import lmdb

from wordclass_builder import MASK_BIT, unpack_wordclass


def candidates(low: str) -> list[str]:
    """Verbatim the order wordclass_builder PASS 2 now uses."""
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


def load_freq(path: Path) -> dict:
    """`count\\tescaped_token` -- the declared format. Parsed as declared, because
    reading the count column as the word is a mistake this lane has already made once."""
    freq = {}
    if not path.exists():
        return freq
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            if line.startswith('#'):
                continue
            p = line.rstrip('\n').split('\t', 1)
            if len(p) == 2 and p[0].isdigit():
                freq[p[1]] = int(p[0])
    return freq


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--corpus', default='data/word_frequencies.txt')
    ap.add_argument('--sample', type=int, default=30)
    a = ap.parse_args()

    freq = load_freq(Path(a.corpus))
    print(f"frequency table: {len(freq):,} tokens\n")

    env = lmdb.open(a.db, readonly=True, max_dbs=32, lock=False)
    fwd = env.open_db(b'forward', create=False)
    wc = env.open_db(b'wordclass', create=False)

    # Every surface with a resolved NOMINAL reading is a possible inheritance source.
    resolved: dict[str, int] = {}
    surfaces: list[str] = []
    with env.begin() as t:
        for k, v in t.cursor(db=fwd):
            s = bytes(k).decode('utf-8', 'replace')
            surfaces.append(s)
            rec = t.get(bytes(v), db=wc)
            if rec and len(rec) == 3 and rec[1]:
                resolved[s.lower()] = rec[1]          # the class mask

    multi = []
    single = 0
    for s in surfaces:
        low = s.lower()
        if not low.endswith('s'):
            continue
        hits = [c for c in candidates(low) if c in resolved]
        if len(hits) > 1:
            multi.append((low, hits))
        elif hits:
            single += 1

    print(f"plurals with exactly ONE resolving candidate : {single:,}  (choice is moot)")
    print(f"plurals with MORE THAN ONE                   : {len(multi):,}"
          f"  <- the population the choice affects\n")
    if not multi:
        print("Nothing to report: the candidate order can never matter on this build.")
        return 0

    # Does the chosen candidate (first in order) agree with the most frequent one?
    agree = disagree = unknown = 0
    examples = []
    for low, hits in multi:
        chosen = hits[0]
        known = [(c, freq[c]) for c in hits if c in freq]
        if len(known) < 2:
            unknown += 1
            continue
        best = max(known, key=lambda kv: kv[1])[0]
        if best == chosen:
            agree += 1
        else:
            disagree += 1
            if len(examples) < a.sample:
                examples.append((low, chosen, freq.get(chosen, 0), best, freq[best]))

    tot = agree + disagree
    print(f"of those, with frequency known for >=2 candidates: {tot:,}")
    if tot:
        print(f"  chosen candidate IS the most frequent : {agree:,}  ({100*agree/tot:.1f}%)")
        print(f"  chosen candidate is NOT               : {disagree:,}  ({100*disagree/tot:.1f}%)")
    print(f"  frequency unknown for 2+ candidates   : {unknown:,}")

    if examples:
        print(f"\ndisagreements (first {len(examples)}) -- plural, chosen(freq), "
              f"most-frequent(freq):")
        for low, ch, cf, bf, bfr in examples:
            print(f"  {low:<20} chose {ch:<16}({cf:>7,})   vs {bf:<16}({bfr:>7,})")

    print("\nREAD THIS AS EVIDENCE, NOT A VERDICT. A disagreement is not automatically an")
    print("error: the more frequent stem is not always the right singular. And there is")
    print("no gold set for wordclass, so this cannot be scored -- it can only be looked")
    print("at. If the disagreement rate is high, the rule needs a real decision; if it")
    print("is negligible, the ordered list is sufficient and this measurement says so.")
    env.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
