"""
phrase_compositionality.py -- MEASURE how "buildable from its words" each phrase is,
to decide per-phrase whether composed assets suffice or a direct asset is needed.

For every phrase it computes:

    compositionality = cos( compose(constituent word vectors),  embed(phrase directly) )

    high (→1)  the phrase IS its parts        (red car, hot water)  -> compose is fine
    low  (→0)  idiom; whole != parts          (hot dog, cast iron)  -> needs a direct asset

Reuses the word vectors ALREADY built by denotative_index (dictionary.denotative
.vecs.f32 + .surfaces.json) — words are not re-embedded. Only the PHRASES are embedded
directly (mpnet). Outputs a distribution + the most/least compositional examples + a
per-phrase CSV, so the routing table (compose vs direct) is designed from data.

  python phrase_compositionality.py --build db\builds\<name> --device cuda
  python phrase_compositionality.py --build db\builds\<name> --sample 20000   # quick look
  python phrase_compositionality.py --selftest                                # math only, no model

Needs: numpy + sentence-transformers (the same env as the build). No faiss.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

# denotative_index (constants + is_embeddable) is imported lazily inside the loaders,
# so the pure-math selftest runs without it (and without faiss/model on the path).


# ---------------------------------------------------------------------------
# Pure helpers (numpy only — unit-testable without the model)
# ---------------------------------------------------------------------------

def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def compose(words: list[str], word_vec: dict) -> tuple[np.ndarray | None, int]:
    """Mean of the constituent word vectors that exist, re-normalized. Returns
    (unit_vector | None, n_found). None if no constituent word is in the index."""
    found = [word_vec[w] for w in words if w in word_vec]
    if not found:
        return None, 0
    return _unit(np.mean(found, axis=0)), len(found)


def load_word_vectors(build_dir: Path) -> dict:
    from denotative_index import SURFACES_NAME, VECS_NAME, EMBEDDING_DIM  # noqa: PLC0415
    surf = json.loads((build_dir / SURFACES_NAME).read_text(encoding="utf-8"))
    vecs = np.fromfile(build_dir / VECS_NAME, dtype="float32").reshape(len(surf), EMBEDDING_DIM)
    return {s: vecs[i] for i, s in enumerate(surf)}


def load_phrases(meta_db: Path) -> list[str]:
    from denotative_index import is_embeddable                            # noqa: PLC0415
    con = sqlite3.connect(str(meta_db))
    try:
        rows = con.execute("SELECT surface FROM meta WHERE kind='phrase'").fetchall()
    finally:
        con.close()
    return [s for (s,) in rows if is_embeddable(s) and len(s.split()) >= 2]


def summarize(scores: np.ndarray) -> str:
    bins = [(-1.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
    labels = ["<0.3 idiom", "0.3-0.5", "0.5-0.7", "0.7-0.8", "0.8-0.9", ">0.9 compositional"]
    out = [f"  n={len(scores):,}  mean={scores.mean():.3f}  median={np.median(scores):.3f}"]
    for (lo, hi), lab in zip(bins, labels):
        m = ((scores >= lo) & (scores < hi)).sum()
        out.append(f"    {lab:<22} {m:>8,}  {100*m/len(scores):5.1f}%")
    comp = (scores >= 0.8).mean() * 100
    idi = (scores < 0.5).mean() * 100
    out.append(f"  => compositional (>=0.8): {comp:.1f}%   idiomatic (<0.5): {idi:.1f}%")
    return "\n".join(out)


# ---------------------------------------------------------------------------

def measure(build_dir: Path, device=None, sample=None, out_csv: Path | None = None):
    from sentence_transformers import SentenceTransformer   # noqa: PLC0415
    from denotative_index import EMBEDDING_MODEL            # noqa: PLC0415
    word_vec = load_word_vectors(build_dir)
    phrases = load_phrases(build_dir / "meta.db")
    print(f"words in index: {len(word_vec):,}   phrases (>=2 words): {len(phrases):,}")

    if sample and sample < len(phrases):
        rng = np.random.default_rng(0)
        phrases = list(rng.choice(phrases, sample, replace=False))
        print(f"sampled {len(phrases):,}")

    # compose from words first; keep only phrases with >=1 constituent found
    comp_vecs, keep, nfound = [], [], []
    for p in phrases:
        v, nf = compose(p.split(), word_vec)
        if v is not None:
            comp_vecs.append(v); keep.append(p); nfound.append(nf)
    print(f"composable (>=1 constituent in index): {len(keep):,}/{len(phrases):,}")
    comp_vecs = np.asarray(comp_vecs, dtype="float32")

    print("embedding phrases directly...")
    model = SentenceTransformer(EMBEDDING_MODEL, device=device)
    direct = model.encode(keep, batch_size=256, convert_to_numpy=True,
                          normalize_embeddings=True, show_progress_bar=True)

    scores = np.sum(comp_vecs * direct, axis=1)     # cos of two unit vectors
    nwords = np.array([len(p.split()) for p in keep])
    nfoundarr = np.array(nfound)

    print("\n=== ALL composable phrases (CONFOUNDED by partial coverage) ===")
    print(summarize(scores))
    print("  note: phrases with n_found < n_words score low as an ARTIFACT (a single")
    print("  content word vs the whole phrase), not because they are idioms.")

    # The clean signal: only phrases whose EVERY word is a content word in the index.
    full = nfoundarr == nwords
    print(f"\n=== FULLY-COMPOSABLE subset — all constituents in index "
          f"({full.sum():,}/{len(keep):,}) — the real signal ===")
    if full.sum():
        print(summarize(scores[full]))
        fidx = np.where(full)[0][np.argsort(scores[full])]
        print("\n most IDIOMATIC (fully-composable — genuine idioms → direct asset):")
        for i in fidx[:15]:
            print(f"    {scores[i]:.2f}  {keep[i]!r}")
        print("\n most COMPOSITIONAL (fully-composable → compose is safe):")
        for i in fidx[-15:]:
            print(f"    {scores[i]:.2f}  {keep[i]!r}")

    if out_csv:
        with open(out_csv, "w", encoding="utf-8") as f:
            f.write("phrase,n_words,n_found,compositionality\n")
            for p, nf, sc in zip(keep, nfound, scores):
                f.write(f"\"{p}\",{len(p.split())},{nf},{sc:.4f}\n")
        print(f"\nwrote {out_csv}  ({len(keep):,} rows)")
    return scores


# ---------------------------------------------------------------------------

def _selftest() -> int:
    wv = {"red": np.array([1., 0., 0.]), "car": np.array([0., 1., 0.]),
          "hot": np.array([0.7, 0.7, 0.]), "dog": np.array([0., 0., 1.])}
    ok = True
    def check(c, m):
        nonlocal ok; print(("  ok  " if c else "  FAIL") + " " + m); ok = ok and c

    v, nf = compose(["red", "car"], wv)
    check(nf == 2 and abs(np.linalg.norm(v) - 1.0) < 1e-6, "compose: mean of 2 words, unit norm")
    v1, _ = compose(["red", "missing"], wv)
    check(np.allclose(v1, _unit(wv["red"])), "compose: skips words not in index")
    check(compose(["a", "b"], wv) == (None, 0), "compose: None when no word found")
    # a compositional phrase: composed ~ direct  -> cos high; idiom -> cos low (synthetic)
    comp = np.asarray([_unit(np.mean([wv["hot"], wv["dog"]], axis=0))])
    direct_comp = comp.copy()                    # pretend direct == composed
    direct_idiom = np.array([[0., 0., 1.]])      # pretend "hot dog" embeds like "dog"-ish elsewhere
    s_comp = float((comp * direct_comp).sum())
    s_idi = float((comp * direct_idiom).sum())
    check(s_comp > 0.99, "cos high when direct==composed (compositional)")
    check(s_idi < s_comp, "cos lower for idiom (direct diverges)")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--build", type=Path)
    ap.add_argument("--device", default=None)
    ap.add_argument("--sample", type=int, default=None)
    ap.add_argument("--out-csv", type=Path, default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    if not a.build:
        raise SystemExit("--build is required (or --selftest)")
    out = a.out_csv or (a.build / "phrase_compositionality.csv")
    measure(a.build, device=a.device, sample=a.sample, out_csv=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
