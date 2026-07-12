"""
spot_check_embeddings.py -- eyeball mpnet neighbour quality on OUR vocabulary
BEFORE committing hours of GPU to the full 245k index.

Raw 768-float vectors are meaningless to preview. What matters is the RELATIONSHIPS:
does the model place `car` near `truck` and far from `decoration`? This samples a
background pool of real surfaces from a build's meta.db, embeds it plus a curated
probe list, and prints each probe word's nearest neighbours drawn FROM YOUR
DICTIONARY -- the true test the full-index brochure can't give you.

Cost: a background of ~5,000 words embeds in ~1-2 min on CPU. No GPU needed to decide.

    C:\...\Python311\python.exe spot_check_embeddings.py --meta db\builds\general_v0.4_char4\meta.db
    ...\python.exe spot_check_embeddings.py --meta ... --words parser mitochondria 000galon

Reads: nothing but meta.db + the model. Writes: nothing. Needs sentence-transformers.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

import numpy as np

MODEL = "all-mpnet-base-v2"
MAX_SURFACE_LEN = 40

# Probes spanning the range where quality is UNKNOWN on our data:
#   common (model's home turf) | technical | corpus-noise tail (ASR junk)
DEFAULT_PROBES = [
    "car", "happy", "murder", "doctor", "rabbit", "big",      # common
    "parser", "server", "memory", "compression", "token",     # our-domain technical
    "mitochondria", "democracy", "invoice",                   # general technical
    "000galon", "birthcap", "0.15x",                          # corpus noise (expect garbage)
]


def is_embeddable(s: str) -> bool:
    return bool(s) and bool(s.strip()) and len(s) <= MAX_SURFACE_LEN and any(c.isalpha() for c in s)


def load_pool(meta_db: str) -> list[str]:
    con = sqlite3.connect(meta_db)
    try:
        rows = con.execute(
            "SELECT surface FROM meta WHERE kind='word' AND utility='CONTENT'").fetchall()
    finally:
        con.close()
    return sorted({r[0] for r in rows if is_embeddable(r[0])})


def main() -> None:
    ap = argparse.ArgumentParser(description="mpnet neighbour spot-check on our vocab")
    ap.add_argument("--meta", required=True)
    ap.add_argument("--background", type=int, default=5000,
                    help="random real surfaces to draw neighbours from")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--words", nargs="*", default=None, help="override probe list")
    ap.add_argument("--device", default=None)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    pool = load_pool(a.meta)
    probes = a.words or DEFAULT_PROBES
    print(f"pool: {len(pool):,} embeddable CONTENT words in {a.meta}")

    rng = np.random.default_rng(a.seed)
    bg = list(rng.choice(pool, min(a.background, len(pool)), replace=False))
    # ensure every probe is present in the background so neighbours are symmetric
    vocab = sorted(set(bg) | set(p for p in probes if p in set(pool)))
    missing = [p for p in probes if p not in set(pool)]
    print(f"background: {len(vocab):,} words embedding now "
          f"(probes not in dictionary: {missing or 'none'})\n")

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL, device=a.device)
    emb = model.encode(vocab, batch_size=256, convert_to_numpy=True,
                       normalize_embeddings=True, show_progress_bar=True)
    idx = {w: i for i, w in enumerate(vocab)}

    # --- raw vector preview (so "preview the vectors" is literally answered) ---
    print("\n=== raw vector preview (768-d, unit-normalised) ===")
    for w in ("car", "truck", "decoration"):
        if w in idx:
            v = emb[idx[w]]
            print(f"  {w:<11} norm={np.linalg.norm(v):.3f}  first5=[{', '.join(f'{x:+.3f}' for x in v[:5])} ...]")
    if "car" in idx and "truck" in idx and "decoration" in idx:
        c, t, d = emb[idx["car"]], emb[idx["truck"]], emb[idx["decoration"]]
        print(f"  cos(car, truck)      = {float(c@t):.3f}   <- want HIGH (denotation)")
        print(f"  cos(car, decoration) = {float(c@d):.3f}   <- EPA said these were neighbours; want LOW")

    # --- neighbours drawn from OUR dictionary ---------------------------------
    print("\n=== nearest neighbours in our vocabulary (cosine) ===")
    for w in probes:
        if w not in idx:
            print(f"  {w:<13} (not in dictionary)"); continue
        sims = emb @ emb[idx[w]]
        order = np.argsort(-sims)
        nbrs = [(vocab[j], float(sims[j])) for j in order if j != idx[w]][:a.k]
        shown = ", ".join(f"{s}:{c:.2f}" for s, c in nbrs)
        print(f"  {w:<13} -> {shown}")

    print("\nRead it: common words should return sensible synonyms; corpus-noise words")
    print("should return low-similarity garbage (which the sim<0.35 prune removes).")
    print("If common words are garbage too, STOP -- do not build the full index.")


if __name__ == "__main__":
    main()
