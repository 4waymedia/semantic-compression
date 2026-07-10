"""
epa_validity.py -- the validity gate for EPA nearest-neighbour queries.

EPA is a 3-dimensional AFFECT space. Nearest-neighbour over it returns
affectively-proximate words, NOT synonyms. For affect-laden words that coincides
with meaning (`happy` -> joy/cheerful/excited) because they sit in the sparse
extremes. For denotative words it does not: they pile into the bland centroid,
where thousands of points are equally close and the top-k is an arbitrary draw.

    car    -> credentials (L2 0.066), attention (0.079), decoration (0.081)
    doctor -> noted (0.025), 'good cuz', 'dining hall', means, 'done yeah'

The retrieval is EXACT -- FlatL2 over 3 dims returns the true nearest points. The
space simply cannot separate them. No index tuning fixes this; the information was
never encoded. See VERSIONS.md and docs/compression/spec-meta-db.md.

THE GATE. Rather than emit noise from the bland centroid, we ABSTAIN when the query
sits in a dense region. The statistic is local density:

    n_r   = number of substrate points within radius r of the query
    conf  = k / max(n_r, k)      # we return k of n_r equally-close candidates
    answer iff conf >= MIN_CONFIDENCE

Read it plainly: "we are returning 5 of 92 equally-close words" -> conf 0.054 ->
the choice is arbitrary -> abstain.

CALIBRATION (measured 2026-07-09 on the 67,936-entry substrate, r=0.25):
    affect words   n_r <= 19   (happy 1, murder 3, joy 10, terrified 19)
    denotative     n_r >= 56   (rabbit 56, doctor 92, car 111, big 319)
A clean separation gap. MIN_CONFIDENCE=0.15 with k=5 admits n_r <= 33.
Substrate-wide abstention at that operating point: **59.4%** (median n_r = 49).
That number is the honest measure of how much of the vocabulary EPA-NN can serve.

WHAT THIS GATE DOES **NOT** DO -- measured 2026-07-09, do not misread it:

    It filters ARBITRARINESS, not HARM. These are different problems.

    doctor  conf=0.065  ABSTAIN                                     (bland collision)
    murder  conf=1.000  ANSWER   [deceived, sodomite, enslavement]
    rape    conf=1.000  ANSWER   [motherfucker, terrorism, asphyxiation]

`L2(murder, sodomite) = 0.167`, n_r=3 -- a TRUE affective neighbour at maximum
confidence. The gate cannot filter it because it is not noise; it is correct.

The reason is structural: **the negative extreme of an affect space is, by
construction, a repository of the most offensive vocabulary.** Warriner/NRC
annotators rated slurs and violence as maximally negative, so they are genuinely the
nearest neighbours of `murder`/`rape`/`kill`. Any system that surfaces EPA
neighbours in that region will surface them. That is a property of the space, not a
defect to patch.

**Therefore: EPA nearest-neighbours must never be surfaced as user-facing output.**
Use them only as an internal RE-RANKING signal over candidates retrieved by a
denotative index (the 768-d `all-mpnet-base-v2` index, `config.FAISS_PATH`, not yet
built). Passing this gate means "not arbitrary" -- it does NOT mean "safe to show".

Deterministic. numpy + lmdb only (no faiss/scipy required). Densities can be
precomputed once and read at O(1) -- pay for structure at build time.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

# --- calibrated constants (see module docstring; do not change without re-measuring)
EPA_NN_RADIUS = 0.25          # L2 radius defining "equally close" in EPA space
EPA_NN_MIN_CONFIDENCE = 0.15  # below this the top-k is an arbitrary draw
EPA_NN_DEFAULT_K = 5

_EPA_STRUCT = struct.Struct("<fff")
_PREFIX = "en|"


# ---------------------------------------------------------------------------
# Pure functions -- trivially testable, no I/O
# ---------------------------------------------------------------------------

def confidence(n_r: int, k: int = EPA_NN_DEFAULT_K) -> float:
    """Fraction of the equally-close candidate set we actually return.
    n_r <= k  -> 1.0 (we return them all; nothing is being arbitrarily chosen)."""
    if k <= 0:
        return 0.0
    return float(min(1.0, k / max(int(n_r), k)))


def is_reliable(n_r: int, k: int = EPA_NN_DEFAULT_K,
                min_confidence: float = EPA_NN_MIN_CONFIDENCE) -> bool:
    """True iff an EPA nearest-neighbour result for this query is meaningful."""
    return confidence(n_r, k) >= min_confidence


def guard(neighbors: list, n_r: int, k: int = EPA_NN_DEFAULT_K,
          min_confidence: float = EPA_NN_MIN_CONFIDENCE) -> tuple[list, float, str]:
    """Apply the gate. Returns (neighbors_or_empty, confidence, reason).

    ABSTAINING IS THE POINT: an empty list is a correct answer meaning "EPA cannot
    distinguish this word's neighbours." Do not fall back to returning them anyway.
    """
    c = confidence(n_r, k)
    if c >= min_confidence:
        return list(neighbors)[:k], c, "ok"
    return [], c, (f"abstain: query sits in a dense EPA region "
                   f"(n_r={n_r} within {EPA_NN_RADIUS}); returning {k} of {n_r} "
                   f"equally-close words would be an arbitrary draw")


# ---------------------------------------------------------------------------
# Substrate access + density
# ---------------------------------------------------------------------------

def load_substrate(epa_lmdb_path: str | Path) -> tuple[list[str], np.ndarray]:
    """Read (surface, EPA) pairs from the epa sub-DB. Strips the 'en|' prefix."""
    import lmdb
    env = lmdb.open(str(epa_lmdb_path), readonly=True, max_dbs=8, lock=False)
    db = env.open_db(b"epa", create=False)
    surfaces, vecs = [], []
    with env.begin() as txn:
        for k, v in txn.cursor(db=db):
            if len(v) != 12:
                continue
            s = k.decode("utf-8", "replace")
            surfaces.append(s[len(_PREFIX):] if s.startswith(_PREFIX) else s)
            vecs.append(_EPA_STRUCT.unpack(v))
    env.close()
    return surfaces, np.asarray(vecs, dtype=np.float32)


def neighbor_density(vectors: np.ndarray, query: np.ndarray,
                     radius: float = EPA_NN_RADIUS) -> int:
    """Count points strictly within `radius` of `query`, excluding the query itself."""
    d = np.linalg.norm(vectors - query, axis=1)
    return int((d < radius).sum()) - 1


def precompute_densities(vectors: np.ndarray, radius: float = EPA_NN_RADIUS,
                         chunk: int = 512) -> np.ndarray:
    """n_r for every point. O(n^2) once at build time, O(1) per query thereafter."""
    n = len(vectors)
    out = np.empty(n, dtype=np.int32)
    for i in range(0, n, chunk):
        blk = vectors[i:i + chunk]
        d = np.linalg.norm(blk[:, None, :] - vectors[None, :, :], axis=2)
        out[i:i + chunk] = (d < radius).sum(axis=1) - 1
    return out


class EPAGate:
    """Query-time gate. Holds surfaces + vectors; densities optional (else computed)."""

    def __init__(self, surfaces: list[str], vectors: np.ndarray,
                 densities: np.ndarray | None = None,
                 radius: float = EPA_NN_RADIUS,
                 min_confidence: float = EPA_NN_MIN_CONFIDENCE):
        self.surfaces = surfaces
        self.vectors = vectors
        self.densities = densities
        self.radius = radius
        self.min_confidence = min_confidence
        self._idx = {s: i for i, s in enumerate(surfaces)}

    @classmethod
    def from_lmdb(cls, path, **kw) -> "EPAGate":
        s, v = load_substrate(path)
        return cls(s, v, **kw)

    def density_for(self, surface: str) -> int | None:
        i = self._idx.get(surface)
        if i is None:
            return None
        if self.densities is not None:
            return int(self.densities[i])
        return neighbor_density(self.vectors, self.vectors[i], self.radius)

    def neighbors(self, surface: str, k: int = EPA_NN_DEFAULT_K
                  ) -> tuple[list[str], float, str]:
        """Gated EPA nearest neighbours -- an INTERNAL re-ranking signal.

        Returns ([], 0.0, reason) when unreliable or when the surface has no EPA
        rating (never guess).

        WARNING: a non-empty result means "not an arbitrary draw". It does NOT mean
        "safe to display". Affect-extreme queries return true affective neighbours
        that are frequently slurs or violence (`murder` -> `sodomite` at conf 1.000),
        because that is what the negative extreme of an affect space contains. Never
        surface these directly; re-rank denotative candidates with them instead.
        """
        i = self._idx.get(surface)
        if i is None:
            return [], 0.0, "abstain: no EPA rating for this surface"
        q = self.vectors[i]
        d = np.linalg.norm(self.vectors - q, axis=1)
        order = np.argsort(d)[1:k + 1]
        raw = [self.surfaces[j] for j in order]
        n_r = self.density_for(surface)
        return guard(raw, n_r, k, self.min_confidence)


# ---------------------------------------------------------------------------
# CLI -- report the operating point on a real substrate
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="EPA nearest-neighbour validity gate")
    p.add_argument("--epa-db", default="../Memory/data/epa_substrate.lmdb")
    p.add_argument("-k", type=int, default=EPA_NN_DEFAULT_K)
    p.add_argument("--sample", type=int, default=3000, help="abstention-rate sample")
    args = p.parse_args()

    gate = EPAGate.from_lmdb(args.epa_db)
    print(f"substrate: {len(gate.surfaces):,} entries  dim={gate.vectors.shape[1]}  "
          f"r={gate.radius}  k={args.k}  min_conf={gate.min_confidence}")

    rng = np.random.default_rng(0)
    samp = rng.choice(len(gate.vectors), min(args.sample, len(gate.vectors)), replace=False)
    counts = np.array([neighbor_density(gate.vectors, gate.vectors[j], gate.radius)
                       for j in samp])
    conf = np.minimum(1.0, args.k / np.maximum(counts, args.k))
    print(f"  median n_r = {int(np.median(counts))}   "
          f"ABSTENTION = {(conf < gate.min_confidence).mean()*100:.1f}%")

    print("\nprobe:")
    for w in ("happy", "murder", "joy", "terrified", "rabbit", "car", "doctor", "big"):
        nb, c, why = gate.neighbors(w, args.k)
        verdict = "ANSWER " if nb else "ABSTAIN"
        print(f"  {w:<11} conf={c:.3f}  {verdict}  {nb if nb else why[:58]}")


if __name__ == "__main__":
    main()
