"""
hybrid_neighbors.py -- retrieve by DENOTATION, re-rank by AFFECT.

The configuration in which both representations do what they are each validated for:

    RETRIEVE  768-d cosine (denotative_index)  -> "what does this word mean?"
    RE-RANK   3-d EPA distance (epa_validity)  -> "which of these feel right?"

EPA is *never* a retrieval key. Three dimensions cannot separate `car` from
`decoration`; they can only order a set that is already denotatively grounded.

THE SAFETY PROPERTY (structural, not a blocklist)
------------------------------------------------
`rerank()` can only reorder candidates -- it can never introduce one. So a term can
appear in the output only if it is a DENOTATIVE neighbour of the query.

    EPA-NN alone:   murder -> [deceived, sodomite, enslavement]   (conf 1.000)
    hybrid:         sodomite is not a denotative neighbour of murder,
                    so it never enters the candidate pool at all.

This is why the affect-extreme harm that the density gate cannot filter
(`epa_validity`: "filters arbitrariness, not harm") is eliminated here by
construction. `test_hybrid_neighbors.py` asserts the no-introduction invariant.

Affect is advisory, never exclusionary: candidates lacking an EPA rating (only
70,249 of 250,083 pool surfaces have one) are KEPT, scored on cosine alone. Never
drop a denotatively-correct word for lacking affect data.

score = cosine - affect_weight * (epa_L2 / EPA_MAX_DIST)

with `affect_weight` small (default 0.25) so denotation dominates and affect breaks
ties. Setting affect_weight=0 gives pure denotative search.
"""
from __future__ import annotations

import math

# Max L2 distance inside the +/-4 EPA cube: sqrt(3 * 8^2)
EPA_MAX_DIST = math.sqrt(3 * (8.0 ** 2))   # 13.856
DEFAULT_AFFECT_WEIGHT = 0.25
DEFAULT_K = 5
DEFAULT_POOL = 50


def epa_distance(a, b) -> float:
    """L2 between two (E,P,A) triples."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def rerank(candidates: list[tuple[str, float]],
           query_epa=None,
           epa_of=None,
           k: int = DEFAULT_K,
           affect_weight: float = DEFAULT_AFFECT_WEIGHT) -> list[tuple[str, float]]:
    """Reorder denotative `candidates` [(surface, cosine)] by affect proximity.

    INVARIANT: the returned surfaces are a SUBSET of the input surfaces. Affect can
    reorder and truncate; it can never introduce a candidate. This is the safety
    property -- see the module docstring.

    `epa_of(surface) -> (E,P,A) | None`. Candidates without EPA (or when the query
    has no EPA) keep their cosine score and are retained, never dropped.
    """
    if affect_weight == 0.0 or query_epa is None or epa_of is None:
        scored = list(candidates)
    else:
        scored = []
        for surface, cos in candidates:
            e = epa_of(surface)
            if e is None:
                scored.append((surface, cos))          # advisory, not exclusionary
            else:
                d = epa_distance(query_epa, e) / EPA_MAX_DIST
                scored.append((surface, cos - affect_weight * d))
    scored.sort(key=lambda t: -t[1])
    return scored[:k]


class HybridNeighbors:
    """Denotative retrieval + affective re-ranking.

    `index`   : denotative_index.DenotativeIndex (768-d cosine)
    `epa_gate`: epa_validity.EPAGate  (used ONLY as an EPA lookup here, not to gate;
                the density gate governs *EPA-only* queries, which this replaces)
    """

    def __init__(self, index, epa_gate=None,
                 affect_weight: float = DEFAULT_AFFECT_WEIGHT):
        self.index = index
        self.epa_gate = epa_gate
        self.affect_weight = affect_weight

    def _epa_of(self, surface):
        g = self.epa_gate
        if g is None:
            return None
        i = g._idx.get(surface)
        return None if i is None else tuple(float(x) for x in g.vectors[i])

    def neighbors(self, surface: str, k: int = DEFAULT_K,
                  pool: int = DEFAULT_POOL) -> tuple[list[str], str]:
        """Top-k denotative neighbours, affect-reordered. ([], reason) if unknown."""
        if not self.index.has(surface):
            return [], "abstain: surface not in the denotative index"
        cands = self.index.search(surface, pool)
        if not cands:
            return [], "abstain: no denotative neighbours"
        ranked = rerank(cands, self._epa_of(surface), self._epa_of,
                        k=k, affect_weight=self.affect_weight)
        return [s for s, _ in ranked], "ok"


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    p = argparse.ArgumentParser(description="hybrid denotative+affect neighbours")
    p.add_argument("words", nargs="+")
    p.add_argument("--index-dir", default="db")
    p.add_argument("--epa-db", default="../Memory/data/epa_substrate.lmdb")
    p.add_argument("-k", type=int, default=DEFAULT_K)
    p.add_argument("--affect-weight", type=float, default=DEFAULT_AFFECT_WEIGHT)
    a = p.parse_args()

    from denotative_index import DenotativeIndex
    idx = DenotativeIndex.load(a.index_dir)
    gate = None
    if Path(a.epa_db).exists():
        from epa_validity import EPAGate
        gate = EPAGate.from_lmdb(a.epa_db)
    hb = HybridNeighbors(idx, gate, a.affect_weight)
    for w in a.words:
        nb, why = hb.neighbors(w, a.k)
        print(f"  {w:<12} {nb if nb else why}")
