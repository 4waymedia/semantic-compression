"""
test_pool_counter.py -- C2 gate: multi-source pooled counting.

Proves (1) a transcripts-only pool reproduces word_frequency_counter's counts on the
same inputs (strict superset, no drift), and (2) weighted merge + dispersion work.

Run from the repo root (R-D-concepts/):
    python -m semantic_compression.test_pool_counter
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

from semantic_compression.config import TRANSCRIPT_DIR
from semantic_compression.corpus_scanner import scan_single
from semantic_compression.word_frequency_counter import count_texts
from semantic_compression.pool_counter import (
    PoolCounts, SourceSpec, count_sources,
)

_N = 3


def test_reproduces_word_frequency_counter():
    files = sorted(Path(TRANSCRIPT_DIR).rglob("*.json"))[:_N]
    assert files, f"no transcripts under {TRANSCRIPT_DIR}"
    # reference: today's path (tokenize+normalize over scan_single texts)
    ref = count_texts(text for f in files for (_v, _c, text, _m) in scan_single(str(f)))
    # pooled transcripts-only, weight 1.0
    pool = count_sources([SourceSpec("transcripts", 1.0, {"limit": _N})], show=False)
    assert dict(pool.merged()) == dict(ref), "pool transcripts drifted from the counter"


def test_weighted_merge_and_dispersion():
    pc = PoolCounts({"a": 1.0, "b": 0.5})
    pc.add("x", "a", 10)
    pc.add("x", "b", 4)
    pc.add("y", "a", 2)
    assert pc.merged_count("x") == round(10 * 1.0 + 4 * 0.5)   # 12
    assert pc.merged_count("y") == 2
    assert pc.dispersion("x") == 2
    assert pc.dispersion("y") == 1
    m = pc.merged()
    assert m["x"] == 12 and m["y"] == 2


def main() -> None:
    tests = [test_reproduces_word_frequency_counter, test_weighted_merge_and_dispersion]
    for t in tests:
        t()
        print(f"[OK] {t.__name__}")
    print(f"\n=== test_pool_counter: {len(tests)}/{len(tests)} PASSED ===")


if __name__ == "__main__":
    main()
