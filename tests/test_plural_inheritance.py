"""B3 — plurals inherit the SINGULAR'S RESOLVED CLASS, not a suffix guess.

THE PROBLEM (coverage diagnosis, 2026-08-29). `companies` (13,777), `dollars` (13,429),
`relationships` (10,191), `stoves`, `bumpers` all have zero word class. Every one of them
has its singular in the vocabulary. The only nominal shape rule runs singular -> `-ies`;
nothing reads plural -> singular.

WHY THE OBVIOUS RULE IS FORBIDDEN. A naive `w[:-1] in vocab -> NOUN` was tried earlier in
this project and retired, because **`stones` and `thinks` are the same shape** — a third
person singular is indistinguishable from a plural by morphology. That rule made every
regular verb a noun. It cannot be reinstated.

WHAT MAKES INHERITANCE DIFFERENT. It does not read the SUFFIX, it reads the SINGULAR'S
ALREADY-RESOLVED CLASS — which was itself derived from distributional evidence. So:

    stove  is NOUN        -> stoves  inherits NOUN        (correct)
    think  is VERB only   -> thinks  inherits nothing     (correct; the trap avoided)
    run    is NOUN|VERB   -> runs    inherits NOUN|VERB    (honest; both readings real)

The shape rule had no way to make that distinction. This does, because a prior pass
already spent evidence on the singular. That requires the build to run in TWO PASSES —
a singular must be resolved before anything can inherit from it.

    python tests/test_plural_inheritance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import lmdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wordclass_builder import unpack_wordclass          # noqa: E402

DEFAULT_DB = (Path(__file__).resolve().parents[1] / 'db' / 'builds'
              / 'elo-browser-v04' / 'dictionary.lmdb')

# Plurals of unambiguous nouns -> must acquire NOUN.
MUST_BE_NOUN = ['stoves', 'bumpers', 'companies', 'dollars', 'relationships',
                'engines', 'tables', 'windows']

# 3rd-person-singular verb forms whose stem is VERB-only -> must NOT acquire NOUN.
# This is the `stones`/`thinks` trap; if any of these reads NOUN the rule has
# regressed to the shape test that was retired.
MUST_NOT_BE_NOUN = ['thinks', 'destroys', 'analyzes', 'sends']

# Ambivalent stems -> the plural inherits the ambivalence. Recording either reading
# alone would be a claim the singular's own evidence did not support.
MUST_BE_AMBIVALENT = ['runs', 'fights']


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
    every = MUST_BE_NOUN + MUST_NOT_BE_NOUN + MUST_BE_AMBIVALENT
    got = read(db_path, every)
    fails = 0

    print('  plurals of nouns -- must acquire NOUN:')
    for w in MUST_BE_NOUN:
        d = got.get(w)
        ok = bool(d) and 'NOUN' in d['classes']
        fails += (not ok)
        print(f"    {'ok ' if ok else 'FAIL'} {w:16}"
              f"{sorted(d['classes']) if d else 'ABSENT'}")

    print('\n  3sg verb forms -- must NOT acquire NOUN (the stones/thinks trap):')
    for w in MUST_NOT_BE_NOUN:
        d = got.get(w)
        ok = (d is None) or ('NOUN' not in d['classes'])
        fails += (not ok)
        print(f"    {'ok ' if ok else 'FAIL'} {w:16}"
              f"{sorted(d['classes']) if d else 'ABSENT'}"
              f"{'' if ok else '  <- shape rule has regressed'}")

    print('\n  ambivalent stems -- plural inherits the ambivalence:')
    for w in MUST_BE_AMBIVALENT:
        d = got.get(w)
        ok = bool(d) and {'NOUN', 'VERB'} <= set(d['classes'])
        fails += (not ok)
        print(f"    {'ok ' if ok else 'FAIL'} {w:16}"
              f"{sorted(d['classes']) if d else 'ABSENT'}")

    print(f"\n{'FAILURES: %d' % fails if fails else 'all plural-inheritance cases pass'}")
    return 1 if fails else 0


def test_plural_inheritance():
    assert run() == 0


if __name__ == '__main__':
    raise SystemExit(run(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB))
