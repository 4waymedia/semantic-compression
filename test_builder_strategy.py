"""
test_builder_strategy.py -- gate for the builder refactor (select_strategy + max_tier).

Proves:
  - max_tier gates build depth (char-2 = max_tier 1 stops at Tier 1; char-3/4 add tiers),
  - a custom select_strategy reorders which entries earn the scarce short-ID tiers,
  - defaults (frequency, max_tier=3) preserve the existing assignment behavior.

Runs entirely in a temp CWD with tiny synthetic inputs + patched tier capacities,
so it never touches the real db/ or data/ artifacts.

Run from the repo root (R-D-concepts/):
    python -m semantic_compression.test_builder_strategy
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

import lmdb
from semantic_compression import dictionary_builder_v03 as db


_WF = "# h\n# h\n1000\tthe\n900\tscience\n800\tapple\n700\tbanana\n600\tcell\n500\tphoton\n"
_PF = "x\t2\t550\t3.5\ta\tb\tc\tcell biology\n"


def _build(tmp, max_tier, strategy=db.score_by_frequency, reserve=1):
    wf = Path(tmp) / "wf.txt"; wf.write_text(_WF, encoding="utf-8")
    pf = Path(tmp) / "pf.txt"; pf.write_text(_PF, encoding="utf-8")
    lm = Path(tmp) / f"d{max_tier}.lmdb"
    st = Path(tmp) / f"s{max_tier}.json"
    return db.build(word_freq_file=wf, phrase_file=pf, lmdb_path=lm, stats_file=st,
                    max_tier=max_tier, select_strategy=strategy,
                    tier1_word_reserve=reserve), lm


def main() -> None:
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="bld_")
    saved_cap = dict(db.TIER_CAPACITY)
    try:
        os.chdir(tmp)                          # relative data/ + db/ land here, not the repo
        db.TIER_CAPACITY = {1: 3, 2: 3, 3: 3}  # tiny caps so depth/overflow is observable

        s3, _ = _build(tmp, 3)
        s1, _ = _build(tmp, 1)
        assert s1["tier2_count"] == 0 and s1["tier3_count"] == 0, "char-2 must stop at Tier 1"
        assert s3["tier2_count"] > 0, "char-4 build should populate Tier 2"
        assert s1["overflow_skipped"] > s3["overflow_skipped"], "shallow build drops more"
        print(f"[OK] max_tier gating  (mt1 tiers {s1['tier1_count']}/{s1['tier2_count']}/"
              f"{s1['tier3_count']} ovf {s1['overflow_skipped']}; mt3 ovf {s3['overflow_skipped']})")

        # custom strategy promotes a mid-frequency word into a char-2 ID
        def strat(surface, freq, kind, n, pmi=0.0):
            return 10**9 if surface == "banana" else float(freq)
        _, lm = _build(tmp, 3, strategy=strat, reserve=0)
        env = lmdb.open(str(lm), readonly=True, max_dbs=2, lock=False)
        fwd = env.open_db(b"forward")
        with env.begin() as t:
            bid = t.get(b"banana", db=fwd).decode()
        env.close()
        assert len(bid) == 2, f"banana should be char-2 under custom strategy, got {bid!r}"
        print(f"[OK] select_strategy reorders  (banana -> {bid!r}, char-2)")

        print("\n=== test_builder_strategy: 2/2 PASSED ===")
    finally:
        os.chdir(cwd)
        db.TIER_CAPACITY = saved_cap


if __name__ == "__main__":
    main()
