"""B1 — the INTERJ class. `yeah` is the most frequent unclassified surface there is.

THE GAP (coverage diagnosis, 2026-08-29). `CLASS` was
`UNKNOWN·NOUN·VERB·MOD·FUNCTION·NAME·NUMERAL·OTHER`, and interjections and discourse
markers fit none of them. Measured on v04:

    yeah 393,619 · maybe 70,464 · gonna 48,227 · cuz 36,894 · mhm 11,265 · yep 9,692

**`yeah` at 393,619 occurrences is the single most frequent surface in the dictionary
with no word class at all.** Not a rare-tail problem — the opposite end of the curve.

WHY THIS COSTS A FORMAT BUMP, and why now. `CLASS` had 8 values in 3 bits: FULL. There
was no free slot, so INTERJ needs a 4th bit, taken from byte 0's reserved pair:

    byte 0 v2  [7:5] class(3)  [4:3] conf(2)  [2] ambivalent  [1:0] reserved
    byte 0 v3  [7:4] class(4)  [3:2] conf(2)  [1] ambivalent  [0]   reserved

The class MASK had exactly one free bit (7), which INTERJ takes. `wordclass_format_version`
-> 3.

Landing it now is deliberate: the Verbalizer is paused, so the churn cost of a contract
change is near zero today and rises the moment it resumes building against v2. Cutting
the loss early beats a refactor later — Paul's call, and the right one.

WHAT INTERJ IS, AND IS NOT. It is a FORM class: a surface that functions as a standalone
utterance or a discourse marker rather than filling an argument slot. It deliberately
does NOT encode which KIND of discourse work the token does — the project already has
that taxonomy in the Tier-0 filler primitives (Q cognitive · R discourse · S validation
· T hedge · U emphasis · V emotional), and duplicating it here would create a second
source of truth for the same distinction. INTERJ says "this is not an argument-taking
word"; the filler classes say what it is doing.

    python tests/test_interj_class.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import lmdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wordclass_builder import unpack_wordclass          # noqa: E402

DEFAULT_DB = (Path(__file__).resolve().parents[1] / 'db' / 'builds'
              / 'elo-browser-v04' / 'dictionary.lmdb')

# The interjections and discourse markers that had no class. Frequencies from
# data/word_frequencies.txt.
INTERJ = ['yeah', 'yep', 'yup', 'mhm', 'uh', 'um', 'huh', 'oh', 'wow', 'hey',
          'okay', 'ok', 'hmm', 'nope', 'ugh', 'oops', 'aha', 'ouch']

# Reduced/colloquial forms that are NOT interjections -- they are contracted verbs and
# conjunctions, and must not be swept in by "looks informal".
NOT_INTERJ = {'gonna': 'VERB',      # going to -- a verb
              'wanna': 'VERB',      # want to
              'cuz': 'FUNCTION'}    # because -- a conjunction

# Must not regress: the classes that already worked.
UNCHANGED = {'dog': 'NOUN', 'think': 'VERB', 'the': 'FUNCTION',
             'quickly': 'MOD', 'israel': 'NAME'}


def read(db_path: Path, words) -> dict:
    env = lmdb.open(str(db_path), readonly=True, lock=False, max_dbs=24)
    fwd = env.open_db(b'forward', create=False)
    wc = env.open_db(b'wordclass', create=False)
    out: dict = {}
    with env.begin() as txn:
        for w in words:
            idb = txn.get(w.encode(), db=fwd)
            if idb is None:
                continue
            rec = txn.get(bytes(idb), db=wc)
            if rec is not None:
                out[w] = unpack_wordclass(bytes(rec))
    env.close()
    return out


def run(db_path: Path = DEFAULT_DB) -> int:
    if not db_path.exists():
        print(f'SKIP: no build at {db_path}')
        return 0
    got = read(db_path, INTERJ + list(NOT_INTERJ) + list(UNCHANGED))
    fails = 0

    print('  INTERJ -- must carry the class:')
    for w in INTERJ:
        d = got.get(w)
        ok = bool(d) and 'INTERJ' in d['classes']
        fails += (not ok)
        print(f"    {'ok ' if ok else 'FAIL'} {w:8}"
              f"{(d['dominant'] if d else 'ABSENT'):9}{sorted(d['classes']) if d else ''}")

    print('\n  NOT interjections -- reduced verb/conjunction forms:')
    for w, want in NOT_INTERJ.items():
        d = got.get(w)
        ok = bool(d) and 'INTERJ' not in d['classes']
        fails += (not ok)
        print(f"    {'ok ' if ok else 'FAIL'} {w:8}"
              f"{(d['dominant'] if d else 'ABSENT'):9}{sorted(d['classes']) if d else ''}"
              f"   (want no INTERJ; ideally {want})")

    print('\n  must not regress:')
    for w, want in UNCHANGED.items():
        d = got.get(w)
        act = d['dominant'] if d else 'ABSENT'
        ok = act == want
        fails += (not ok)
        print(f"    {'ok ' if ok else 'FAIL'} {w:8}{act:9}want {want}")

    print(f"\n{'FAILURES: %d' % fails if fails else 'all INTERJ cases pass'}")
    return 1 if fails else 0


def test_interj():
    assert run() == 0


if __name__ == '__main__':
    raise SystemExit(run(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB))
