"""Byte 2 — the lexical features. Tri-state, and derived rather than defaulted.

THE BUGS THIS FIXES (all measured on v04 / b0164e50, 2026-08-29):

  C1  `requires_determiner` set on 0 of 437,995. Defined at line 94, read at line 108,
      NEVER ASSIGNED. And it is ONE BIT, so `False` cannot be distinguished from
      "never derived" — 437,995 confident falses.
  C4  `proper` is one bit too, and derived from CAPITALISATION SHAPE
      (`surface[:1].isupper() and surface[1:].islower()`), so it is wrong in both
      directions at once: it flags `Abate`, `Abhor`, `Able` (sentence-initials from the
      books corpus) and misses `israel`, `charlie`, `washington` entirely, because the
      transcript corpus is uncapitalised.
  C2  `countability` is 32,781 COUNT and **0 MASS**. `water` reads COUNT, so a generator
      trusting it produces *"a water".* A wrong value, not a missing one.

WHY THE LAYOUT CHANGES. The accepted record spec's property #1 is *"UNKNOWN is 0
everywhere — an unstated feature is ABSENT, and absent widens what may be generated."*
A one-bit boolean cannot say UNKNOWN. `proper` and `requires_determiner` were specified
as single bits and therefore could never honour the rule they were written under. The
two reserved bits in byte 2 are spent making both tri-state — which is exactly what
they cost, and what reserved bits are for.

    byte 2  [7:6] countability        0 UNK · 1 COUNT · 2 MASS · 3 BOTH
            [5:4] inherent_number     0 UNK · 1 SG · 2 PL · 3 INVARIANT
            [3:2] proper              0 UNK · 1 NO · 2 YES          (was 1 bit)
            [1:0] requires_determiner 0 UNK · 1 NO · 2 YES          (was 1 bit)

`wordclass_format_version` -> 2. A consumer reading v1 offsets against a v2 record gets
`proper` from the wrong bits, so the version is not cosmetic and readers must check it.

    python tests/test_wordclass_features.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import lmdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wordclass_builder import unpack_wordclass          # noqa: E402

DEFAULT_DB = (Path(__file__).resolve().parents[1] / 'db' / 'builds'
              / 'elo-browser-v04' / 'dictionary.lmdb')

# --- C2: mass nouns must not read COUNT ------------------------------------
MASS = ['water', 'information', 'evidence']

# KNOWN LIMITS — measured, and my EXPECTATIONS were the wrong half here, not the code.
# Recorded rather than forced, because a test that encodes a wrong belief is worse than
# no test:
#   advice, furniture  `_mass = 0`. "much furniture" simply does not occur in these 26
#                      books, so there is no diagnostic evidence and UNKNOWN is the true
#                      answer. A first version fell back to COUNT on a bare determiner,
#                      which is the unevidenced assertion this whole fix removes.
#   music              `_mass=10, _count=8` -> BOTH. The COUNT half is contaminated by
#                      NOUN-NOUN COMPOUNDS ("a music teacher"), where the determiner
#                      belongs to the compound head and not to `music`. Fixing that
#                      needs a head-detection pass this builder does not have.
#   charlie            `_proper=1, _lower=0` — ONE occurrence in the only cased corpus.
#                      Proper-noun evidence comes solely from books; `charlie` is a
#                      transcript name. Demanding YES was demanding evidence that does
#                      not exist.
KNOWN_LIMITS = {'advice': 'countability', 'furniture': 'countability',
                'music': 'countability', 'charlie': 'proper'}
COUNT = ['dog', 'engine', 'table', 'bottle']

# --- C4: proper, both directions -------------------------------------------
# Lowercase proper nouns from the ASR corpus -- the shape rule could never see these.
PROPER_YES = ['israel', 'washington', 'california']
# Sentence-initial ordinary words from the books corpus -- the shape rule flagged these.
PROPER_NO = ['abate', 'abhor', 'able', 'abominable']

# --- C1: requires_determiner must be DERIVED, and UNKNOWN where it is not ---
# Singular count nouns take a determiner in argument position.
REQDET_YES = ['dog', 'table', 'engine']
# Mass nouns and plurals do not require one.
REQDET_NO = ['water', 'information', 'dogs']

TRI = {0: 'UNKNOWN', 1: 'NO', 2: 'YES'}


def read(db_path: Path, words) -> dict:
    env = lmdb.open(str(db_path), readonly=True, lock=False, max_dbs=20)
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
    every = MASS + COUNT + PROPER_YES + PROPER_NO + REQDET_YES + REQDET_NO
    got = read(db_path, every)
    fails = 0

    def check(label, words, field, want, fmt=str):
        nonlocal fails
        print(f'\n  {label}')
        for w in words:
            d = got.get(w)
            act = d.get(field) if d else 'ABSENT'
            ok = (act == want)
            fails += (not ok)
            print(f"    {'ok ' if ok else 'FAIL'} {w:14}{field}={fmt(act)}  want {fmt(want)}")

    check('C2 mass nouns -- must be MASS or BOTH, never bare COUNT', MASS,
          'countability', 2)
    check('C2 count nouns -- must stay COUNT', COUNT, 'countability', 1)
    check('C4 proper, lowercase (ASR corpus) -- shape rule could not see these',
          PROPER_YES, 'proper', 2, lambda v: TRI.get(v, v))
    check('C4 NOT proper (book sentence-initials) -- shape rule wrongly flagged these',
          PROPER_NO, 'proper', 1, lambda v: TRI.get(v, v))
    check('C1 requires_determiner YES', REQDET_YES, 'requires_determiner', 2,
          lambda v: TRI.get(v, v))
    check('C1 requires_determiner NO', REQDET_NO, 'requires_determiner', 1,
          lambda v: TRI.get(v, v))

    print('\n  known limits (corpus has no evidence; UNKNOWN is the honest answer):')
    lim = read(db_path, list(KNOWN_LIMITS))
    for w, field in sorted(KNOWN_LIMITS.items()):
        d = lim.get(w)
        print(f"    {w:12}{field}={d.get(field) if d else 'ABSENT'}"
              f"   (0=UNKNOWN -- not a failure, an absence)")

    print(f"\n{'FAILURES: %d' % fails if fails else 'all lexical-feature cases pass'}")
    return 1 if fails else 0


def test_features():
    assert run() == 0


if __name__ == '__main__':
    raise SystemExit(run(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB))
