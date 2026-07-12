"""
query_denotative.py -- read neighbours from the finalized 768-d index.

This is the real test the 5k-sample spot-check couldn't give: neighbours drawn from
the full finalized vocabulary. `car -> truck` should appear now that the pool holds
the common words. Also previews the two derived-artifact controls:

  * lowercase dedupe   -- collapses `Relieved`/`relieved` case-variant near-dupes
  * sim<0.35 prune     -- drops the low-similarity tail

Reads the index (needs faiss). No model, no GPU -- just array reads.

  python query_denotative.py --out db car truck happy murder doctor compression server
  python query_denotative.py --out db --dedupe --prune 0.35 car doctor
"""
from __future__ import annotations

import argparse

from denotative_index import DenotativeIndex

# probes spanning common / our-domain / general-technical
DEFAULT = ["car", "happy", "murder", "doctor", "democracy", "compression",
           "server", "memory", "token", "invoice", "big", "rabbit"]


def dedupe_lower(neighbors, k):
    """Collapse case variants and the query's own casings; keep highest sim."""
    seen, out = set(), []
    for surf, sim in neighbors:
        key = surf.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((surf, sim))
        if len(out) >= k:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="query the denotative index")
    ap.add_argument("words", nargs="*", default=None)
    ap.add_argument("--out", default="db")
    ap.add_argument("-k", type=int, default=8)
    ap.add_argument("--prune", type=float, default=0.0, help="drop sim < this")
    ap.add_argument("--dedupe", action="store_true", help="collapse case variants")
    a = ap.parse_args()

    idx = DenotativeIndex.load(a.out)
    print(f"index: {len(idx.surfaces):,} surfaces\n")
    for w in (a.words or DEFAULT):
        if not idx.has(w):
            print(f"  {w:<13} (not in the embedded slice)"); continue
        nb = idx.search(w, pool=max(a.k * 4, 40))
        if a.prune:
            nb = [(s, c) for s, c in nb if c >= a.prune]
        nb = dedupe_lower(nb, a.k) if a.dedupe else nb[:a.k]
        print(f"  {w:<13} -> " + ", ".join(f"{s}:{c:.2f}" for s, c in nb))


if __name__ == "__main__":
    main()
