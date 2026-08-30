"""Temporality gate — expectations FIRST, behaviour measured against them.

WHY THIS FILE EXISTS (2026-08-29, Paul: "you can't drop things without testing and
verifying first" / "No assumptions are allowed").

The temporality gate was changed three times in one session with no test. Each change
was justified by a handful of words chosen AFTER seeing the failure, which is choosing
the test set to fit the answer. It went exactly as that method predicts:

  1. Blanket `VERB -> PROCESS` gave `stone` an aspect it does not have.
  2. Gating on ambivalence fixed `stone` and silently destroyed every irregular past --
     `sent`, `went`, `said`, `saw`, `came`, `took`, `gave`. 41 of 51 in a probe list.
     Coverage fell 15.2% -> 13.9% and the drop was NARRATED as precision without a
     single dropped surface being examined.
  3. The irregular-past table written to fix (2) was typed from memory, not derived,
     and broke 17 live surfaces whose base form is spelled like their past
     (`run`, `read`, `cut`, `put`, `set`, `hurt`, `cost`, ...). `run` went from
     correctly UNKNOWN to wrongly OUTCOME.

Every one of those was found by MEASURING, and every one shipped because the
measurement came after the change. So: expectations are declared here, in advance, and
a change to the gate is judged by this file rather than by argument.

THE CASES ARE THE CONTRACT. `xfail_*` entries are KNOWN-WRONG behaviour recorded
honestly rather than deleted -- an unfixed case must stay visible, not be quietly
dropped from the suite so the suite goes green.

    python -m pytest tests/test_temporality.py -v
    python tests/test_temporality.py            # standalone, no pytest needed
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import lmdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_DB = Path(__file__).resolve().parents[1] / 'db' / 'builds' / 'elo-browser-v04' / 'dictionary.lmdb'
REC2 = struct.Struct('<BB')
TEMPORAL_NAME = {0: 'UNKNOWN', 1: 'STATE', 2: 'PROCESS', 3: 'EVENT',
                 4: 'OUTCOME', 5: 'CONDITION'}

# ---------------------------------------------------------------------------
# EXPECTATIONS. Declared before the run, with the reason each is what it is.
# ---------------------------------------------------------------------------

# Unambiguous verbs, base form: aspect is PROCESS (the unmarked aspect).
BASE_VERBS = {
    'think': 'PROCESS', 'destroy': 'PROCESS', 'send': 'PROCESS',
    'build': 'PROCESS', 'analyze': 'PROCESS',
}

# Regularly inflected: the inflection IS the aspect marker, so it is read off the
# suffix even when the surface is also a noun/adjective (`a measured response`).
INFLECTED = {
    'walking': 'PROCESS', 'running': 'PROCESS',
    'walked': 'OUTCOME', 'measured': 'OUTCOME', 'reached': 'OUTCOME',
    'uses': 'STATE',
}

# Concrete nouns that happen to inflect like verbs. The paradigm layer alone called
# every one of these a VERB; determiner context is what separates them. They must
# carry NO aspect -- this is the `stone` class that started the whole investigation.
CONCRETE_NOUNS = {
    'stone': 'UNKNOWN', 'bridge': 'UNKNOWN', 'window': 'UNKNOWN',
    'house': 'UNKNOWN', 'water': 'UNKNOWN', 'table': 'UNKNOWN',
    'engine': 'UNKNOWN',
}

# Genuinely both, base form. A static channel cannot know which reading applies to a
# token, so declining is the CORRECT answer -- not a coverage gap.
AMBIVALENT_BASE = {
    'run': 'UNKNOWN', 'fight': 'UNKNOWN', 'work': 'UNKNOWN',
    'change': 'UNKNOWN', 'watch': 'UNKNOWN',
}

# KNOWN-WRONG, recorded not hidden. Irregular pasts are unambiguously completed
# aspect and currently read UNKNOWN, because `_is_inflected` is a suffix test and
# these carry no suffix. The naive fix (a hand-typed table) is REJECTED: it forces
# OUTCOME onto the 17 invariant homographs whose base form is spelled identically.
# A correct fix must distinguish `run`-the-base from `run`-the-participle, which a
# surface-keyed channel cannot do by spelling alone. Left failing on purpose.
XFAIL_IRREGULAR_PAST = {
    'sent': 'OUTCOME', 'went': 'OUTCOME', 'said': 'OUTCOME', 'saw': 'OUTCOME',
    'came': 'OUTCOME', 'took': 'OUTCOME', 'gave': 'OUTCOME', 'slept': 'OUTCOME',
    'thought': 'OUTCOME', 'wrote': 'OUTCOME', 'built': 'OUTCOME',
}

# Surfaces a hand-written irregular table would corrupt. These are the REGRESSION
# GUARD for any future attempt: base form and past form are the same string, so
# forcing OUTCOME on the spelling is wrong for the base reading.
INVARIANT_HOMOGRAPHS = ['run', 'read', 'cut', 'put', 'set', 'hurt', 'cost',
                        'hit', 'shut', 'spread', 'beat', 'quit', 'let']

GROUPS = [
    ('base verbs', BASE_VERBS, False),
    ('regularly inflected', INFLECTED, False),
    ('concrete nouns (must decline)', CONCRETE_NOUNS, False),
    ('ambivalent base (must decline)', AMBIVALENT_BASE, False),
    ('irregular past', XFAIL_IRREGULAR_PAST, True),
]


def read_temporal(db_path: Path) -> dict:
    """surface -> temporal name, for every surface named in the expectations."""
    wanted: set = set(INVARIANT_HOMOGRAPHS)
    for _, cases, _ in GROUPS:
        wanted |= set(cases)
    env = lmdb.open(str(db_path), readonly=True, lock=False, max_dbs=16)
    fwd = env.open_db(b'forward', create=False)
    vf = env.open_db(b'vfacets', create=False)
    out: dict = {}
    with env.begin() as txn:
        for w in sorted(wanted):
            idb = txn.get(w.encode(), db=fwd)
            if idb is None:
                continue
            rec = txn.get(bytes(idb), db=vf)
            if rec is None:
                continue
            out[w] = TEMPORAL_NAME[REC2.unpack(bytes(rec))[0] & 0b111]
    env.close()
    return out


def run(db_path: Path = DEFAULT_DB) -> int:
    if not db_path.exists():
        print(f'SKIP: no build at {db_path}')
        return 0
    got = read_temporal(db_path)
    hard_fail = 0
    for label, cases, xfail in GROUPS:
        bad = {w: (exp, got.get(w, 'ABSENT'))
               for w, exp in cases.items() if got.get(w, 'ABSENT') != exp}
        tag = 'xfail' if xfail else ('FAIL' if bad else 'pass')
        print(f'{tag:>6}  {label:<34}{len(cases)-len(bad)}/{len(cases)}')
        for w, (exp, act) in sorted(bad.items()):
            print(f'          {w:12}expected {exp:<9}got {act}')
        if bad and not xfail:
            hard_fail += len(bad)
    print('\n  invariant homographs (regression guard -- none may read OUTCOME):')
    corrupted = [w for w in INVARIANT_HOMOGRAPHS if got.get(w) == 'OUTCOME']
    if corrupted:
        print(f'    FAIL: forced to OUTCOME -> {", ".join(corrupted)}')
        hard_fail += len(corrupted)
    else:
        print(f'    pass: {len([w for w in INVARIANT_HOMOGRAPHS if w in got])} checked, none corrupted')
    print(f"\n{'FAILURES: %d' % hard_fail if hard_fail else 'all non-xfail cases pass'}")
    return 1 if hard_fail else 0


def test_temporality():
    assert run() == 0


if __name__ == '__main__':
    raise SystemExit(run(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB))
