"""`wordclass.dominant` must be EVIDENCE-derived. Twenty words a human can check.

WHY (integration lane finding, 2026-08-29). `dominant` was:

    order = ('VERB', 'NOUN', 'MOD', 'FUNCTION', 'NAME', 'NUMERAL', 'OTHER')
    dominant = next((c for c in order if c in classes), 'UNKNOWN')

— a fixed priority with no evidence comparison anywhere in it, under a comment that
claimed "the highest-evidence class, ties resolved by a fixed priority". The code and
its own docstring disagreed, and the docstring is what two lanes were about to build
against.

Measured consequence: `dog`, `stone`, `water`, `run`, `house` were BYTE IDENTICAL
(`54 03 40`) — dominant=VERB, confidence=CORPUS, ambivalent=True. Four common nouns
recorded as verbs at a confidence that reads as *measured*. Generation morphology picks
a paradigm from `dominant`; it would have inflected `stone`, `water` and `house` as
verbs, confidently.

The finding closed with: *"step 1 completes when `dominant` is evidence-derived and a
census reports per-class counts that a human can sanity-check on twenty common words.
That check is twenty seconds and would have caught this."*

This file IS that check, run automatically instead of hoped for. It is deliberately
made of ordinary words with obvious answers — a test whose failure is legible without
reading the builder.

THE HONEST-UNKNOWN CASES MATTER AS MUCH AS THE RIGHT ANSWERS. `watch` and `change` are
expected to be UNKNOWN: the evidence does not separate their noun and verb readings, and
recording either would be a claim the data does not support. A future change that makes
them confidently NOUN or VERB should FAIL here until it can show the evidence.

    python tests/test_wordclass_dominance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import lmdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wordclass_builder import unpack_wordclass          # noqa: E402

DEFAULT_DB = (Path(__file__).resolve().parents[1] / 'db' / 'builds'
              / 'elo-browser-v04' / 'dictionary.lmdb')

# surface -> expected dominant. Chosen for obviousness, not to flatter the builder.
EXPECTED = {
    # unambiguous nouns that also inflect like verbs -- the exact failure class
    'dog': 'NOUN', 'stone': 'NOUN', 'water': 'NOUN', 'house': 'NOUN', 'book': 'NOUN',
    # unambiguous nouns
    'table': 'NOUN', 'engine': 'NOUN',
    # unambiguous verbs
    'think': 'VERB', 'destroy': 'VERB', 'send': 'VERB', 'build': 'VERB',
    'analyze': 'VERB',
    # closed class
    'the': 'FUNCTION',
    # modifier
    'quickly': 'MOD',
}

# Genuinely balanced N/V: the evidence does not separate the readings, so UNKNOWN is
# the correct record. Asserted explicitly so a regression toward guessing fails.
EXPECTED_UNKNOWN = ('watch', 'change')

# These must never again be byte-identical to each other -- that identity WAS the bug.
MUST_DIFFER = ('dog', 'stone', 'water', 'run', 'house')


def read(db_path: Path) -> dict:
    env = lmdb.open(str(db_path), readonly=True, lock=False, max_dbs=20)
    fwd = env.open_db(b'forward', create=False)
    wc = env.open_db(b'wordclass', create=False)
    out: dict = {}
    with env.begin() as txn:
        for w in set(EXPECTED) | set(EXPECTED_UNKNOWN) | set(MUST_DIFFER):
            idb = txn.get(w.encode(), db=fwd)
            if idb is None:
                continue
            rec = txn.get(bytes(idb), db=wc)
            if rec is None:
                continue
            out[w] = (bytes(rec), unpack_wordclass(bytes(rec)))
    env.close()
    return out


def run(db_path: Path = DEFAULT_DB) -> int:
    if not db_path.exists():
        print(f'SKIP: no build at {db_path}')
        return 0
    got = read(db_path)
    fails = 0

    print(f"  {'word':11}{'expected':10}{'actual':10}")
    for w, exp in sorted(EXPECTED.items()):
        act = got.get(w, (None, {'dominant': 'ABSENT'}))[1]['dominant']
        ok = act == exp
        fails += (not ok)
        print(f"  {'ok ' if ok else 'FAIL'} {w:9}{exp:10}{act:10}")

    print('\n  honest-UNKNOWN cases (evidence does not separate the readings):')
    for w in EXPECTED_UNKNOWN:
        act = got.get(w, (None, {'dominant': 'ABSENT'}))[1]['dominant']
        ok = act == 'UNKNOWN'
        fails += (not ok)
        print(f"  {'ok ' if ok else 'FAIL'} {w:9}{'UNKNOWN':10}{act:10}"
              f"{'' if ok else '  <- a claim the evidence does not support'}")

    print('\n  regression guard -- these five were byte-identical (54 03 40):')
    recs = {w: got[w][0] for w in MUST_DIFFER if w in got}
    if len(set(recs.values())) <= 1:
        print(f'    FAIL: still identical -> {[w for w in recs]}')
        fails += 1
    else:
        for w, r in recs.items():
            print(f"    {w:8}{' '.join(f'{b:02x}' for b in r)}")
        print(f'    ok: {len(set(recs.values()))} distinct records across {len(recs)} words')

    print(f"\n{'FAILURES: %d' % fails if fails else 'all dominance cases pass'}")
    return 1 if fails else 0


def test_dominance():
    assert run() == 0


if __name__ == '__main__':
    raise SystemExit(run(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB))
