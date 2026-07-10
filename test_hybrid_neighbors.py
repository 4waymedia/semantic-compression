"""
test_hybrid_neighbors.py -- retrieve by denotation, re-rank by affect.

The load-bearing test is `test_affect_can_never_introduce_a_candidate`. It encodes
the safety property: EPA can reorder a denotatively-grounded set but can never add
to it. That is what structurally prevents `murder -> sodomite` -- a term the density
gate CANNOT filter, because it is a true affective neighbour at confidence 1.000.

Pure arithmetic; needs neither faiss nor sentence-transformers. The real index is
exercised only if it has been built.

    python test_hybrid_neighbors.py     (from semantic_compression/)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

from hybrid_neighbors import (  # noqa: E402
    DEFAULT_AFFECT_WEIGHT, EPA_MAX_DIST, HybridNeighbors, epa_distance, rerank,
)

# a denotative candidate pool for "murder" as mpnet would plausibly return it
MURDER_POOL = [("homicide", 0.91), ("killing", 0.88), ("manslaughter", 0.84),
               ("assassination", 0.80), ("crime", 0.71)]

_EPA = {
    "murder": (-3.52, -1.62, 1.24),
    "homicide": (-3.40, -1.50, 1.10),
    "killing": (-3.30, -1.40, 1.30),
    "manslaughter": (-3.10, -1.20, 0.90),
    "assassination": (-3.20, -1.60, 1.40),
    "crime": (-2.50, -0.90, 0.60),
    # a TRUE affective neighbour of murder (L2 0.167) that is NOT denotative
    "sodomite": (-3.43, -1.50, 1.17),
}


def _epa_of(s):
    return _EPA.get(s)


def test_affect_can_never_introduce_a_candidate():
    """THE safety property. `sodomite` is affectively nearest to `murder`
    (L2=0.167) yet is not in the denotative pool -> it can never be returned."""
    assert epa_distance(_EPA["murder"], _EPA["sodomite"]) < 0.2, "premise: affect-near"
    out = rerank(MURDER_POOL, _EPA["murder"], _epa_of, k=5)
    surfaces = {s for s, _ in out}
    assert "sodomite" not in surfaces, "affect must never introduce a candidate"
    assert surfaces <= {s for s, _ in MURDER_POOL}, "output must be a subset of the pool"


def test_rerank_is_a_subset_and_respects_k():
    out = rerank(MURDER_POOL, _EPA["murder"], _epa_of, k=3)
    assert len(out) == 3
    assert {s for s, _ in out} <= {s for s, _ in MURDER_POOL}


def test_affect_reorders_within_the_pool():
    """`assassination` is affectively closer to murder than `crime`, and their
    cosines differ by 0.09 -- enough affect weight must be able to reorder them."""
    strong = rerank(MURDER_POOL, _EPA["murder"], _epa_of, k=5, affect_weight=3.0)
    order = [s for s, _ in strong]
    assert order.index("assassination") < order.index("crime")


def test_missing_epa_is_kept_not_dropped():
    """250,083 pool surfaces; only 70,249 have EPA. Never drop a denotatively
    correct word for lacking affect data."""
    pool = MURDER_POOL + [("unrated_word", 0.95)]
    out = rerank(pool, _EPA["murder"], _epa_of, k=6)
    assert "unrated_word" in {s for s, _ in out}
    # highest cosine, no affect penalty -> should rank first
    assert out[0][0] == "unrated_word"


def test_zero_affect_weight_is_pure_denotation():
    out = rerank(MURDER_POOL, _EPA["murder"], _epa_of, k=5, affect_weight=0.0)
    assert [s for s, _ in out] == [s for s, _ in MURDER_POOL]  # cosine order preserved


def test_no_query_epa_falls_back_to_cosine():
    out = rerank(MURDER_POOL, None, _epa_of, k=5)
    assert [s for s, _ in out] == [s for s, _ in MURDER_POOL]


def test_epa_penalty_is_bounded():
    """affect_weight bounds how far affect can move a candidate: the penalty is at
    most affect_weight (when epa_L2 == EPA_MAX_DIST)."""
    far = (4.0, 4.0, 4.0)
    near = (-3.52, -1.62, 1.24)
    d = epa_distance(near, far) / EPA_MAX_DIST
    assert 0.0 <= d <= 1.0
    penalty = DEFAULT_AFFECT_WEIGHT * d
    assert penalty <= DEFAULT_AFFECT_WEIGHT + 1e-9


def test_real_index_if_built():
    from denotative_index import INDEX_NAME
    if not (Path("db") / INDEX_NAME).exists():
        print("  [skip] 768-d index not built yet "
              "(python denotative_index.py --meta <build>/meta.db --out db)")
        return
    from denotative_index import DenotativeIndex
    from epa_validity import EPAGate
    idx = DenotativeIndex.load("db")
    gate = (EPAGate.from_lmdb("../Memory/data/epa_substrate.lmdb")
            if Path("../Memory/data/epa_substrate.lmdb").exists() else None)
    hb = HybridNeighbors(idx, gate)
    nb, why = hb.neighbors("car", 5)
    assert nb, f"'car' must return denotative neighbours, got: {why}"
    assert "decoration" not in nb, "the EPA-collision must not survive the hybrid"


def main() -> None:
    tests = [test_affect_can_never_introduce_a_candidate,
             test_rerank_is_a_subset_and_respects_k,
             test_affect_reorders_within_the_pool,
             test_missing_epa_is_kept_not_dropped,
             test_zero_affect_weight_is_pure_denotation,
             test_no_query_epa_falls_back_to_cosine,
             test_epa_penalty_is_bounded,
             test_real_index_if_built]
    for t in tests:
        t()
        print(f"[OK] {t.__name__}")
    print(f"\n=== test_hybrid_neighbors: {len(tests)}/{len(tests)} PASSED ===")


if __name__ == "__main__":
    main()
