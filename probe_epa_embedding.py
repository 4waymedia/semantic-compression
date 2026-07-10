"""
probe_epa_embedding.py -- three measurements that decide EPA channel 2's fate.

Run BEFORE building anything. The decision rules below are PRE-REGISTERED: they are
stated here, before the numbers exist, so the result cannot be rationalised after the
fact. Write the outcome into docs/compression/spec-meta-db.md either way.

CONTEXT
-------
Phrase EPA today is `epa_match._compose` = elementwise mean over RATED constituents.
Measured consequences (2026-07-09):
    good / not good         -> IDENTICAL   (`not` is unrated, silently dropped)
    dog bites man / man bites dog -> IDENTICAL (mean is permutation-invariant)
    69.9% of filled EPA entries are mean-composed this way
    43.9% of the vocabulary has no EPA at all
The proposal is to replace mean-composition with a projection from the surface's own
768-d mpnet embedding, and to impute EPA for unrated surfaces the same way.

THE NOISE CEILING (this is the point most analyses miss)
--------------------------------------------------------
The substrate's own `meta.v1_matrix` records Warriner vs NRC-VAD agreement on their
13,812-word overlap. A model cannot beat the data's agreement with itself:

    axis   cross-source r    R^2 CEILING
    E      0.814             0.663
    A      0.613             0.376
    P      0.328             0.108      <- unpredictable AND unmeasurable

So do NOT compare R^2 to 1.0. Compare it to the ceiling. R^2(E)=0.60 is 90% of
everything achievable, not a mediocre score.

PRE-REGISTERED DECISION RULES
-----------------------------
P1 (negation): let A = cos(w, "not "+w), B = cos("not "+w, antonym(w)).
    If B > A for a MAJORITY of pairs, mpnet carries negation -> embedding-composed
    phrase EPA handles it. If A > B (the expected outcome for sentence encoders),
    mpnet does NOT carry negation. This does not block the fix: we already have a
    validated deterministic `Claim.polarity` (affirmed|negated, 11/11 tests). Apply
    it SYMBOLICALLY on top of the embedding projection. P1 decides whether the
    symbolic override is REQUIRED, not whether the fix is possible.

P2 (word order): let d = cos(svo, permuted), null = cos(svo, unrelated).
    Mean-composition scores exactly 1.000 here (permutation-invariant), so ANY
    d < 1.0 is strictly better. Adopt embedding composition iff d is well below 1.0
    and clearly above `null` (i.e. it moves the vector without destroying it).

P3 (imputation): ridge 768 -> EPA, fit on directly-rated single words, held out 20%.
    ADOPT imputation for an axis iff  R^2_heldout >= 0.65 * R^2_ceiling.
      E: adopt iff R^2 >= 0.431
      A: adopt iff R^2 >= 0.244
      P: DO NOT ADOPT under any outcome. Ceiling 0.108 -- the sources barely agree;
         an imputed P would be noise wearing a number. Leave P NULL.
    Any imputed value MUST carry its own `prov` tag. Predicted affect must never be
    indistinguishable from a human rating (O-EPA9: never fake-neutral).

Requires: sentence-transformers, numpy. (`--synthetic` exercises the harness with
random vectors and no model, to verify the code before spending GPU time.)

    python probe_epa_embedding.py --synthetic          # harness check, no model
    python probe_epa_embedding.py --device cuda        # the real measurement
"""
from __future__ import annotations

import argparse
import struct
import sys

import numpy as np

EPA_LMDB = "../Memory/data/epa_substrate.lmdb"
MODEL = "all-mpnet-base-v2"

# R^2 ceilings from substrate meta.v1_matrix (Warriner vs NRC-VAD, 13,812 overlap)
CEILING = {"E": 0.663, "A": 0.376, "P": 0.108}
ADOPT_FRACTION = 0.65
ADOPT_THRESHOLD = {k: round(ADOPT_FRACTION * v, 3) for k, v in CEILING.items()}

# P1: curated lexical antonyms (affect-antonyms would beg the question)
ANTONYMS = [
    ("good", "bad"), ("happy", "sad"), ("safe", "dangerous"), ("kind", "cruel"),
    ("strong", "weak"), ("fast", "slow"), ("hot", "cold"), ("big", "small"),
    ("clean", "dirty"), ("easy", "hard"), ("rich", "poor"), ("true", "false"),
    ("light", "dark"), ("early", "late"), ("open", "closed"), ("full", "empty"),
    ("calm", "anxious"), ("honest", "dishonest"), ("useful", "useless"),
    ("healthy", "sick"), ("brave", "cowardly"), ("generous", "greedy"),
    ("polite", "rude"), ("wise", "foolish"),
]

# P2: SVO sentences whose permutation preserves the bag of words exactly
SVO = [
    ("the dog bites the man", "the man bites the dog"),
    ("the server crashed the process", "the process crashed the server"),
    ("the manager blamed the engineer", "the engineer blamed the manager"),
    ("the parser reads the token", "the token reads the parser"),
    ("the child taught the teacher", "the teacher taught the child"),
    ("the client paid the lawyer", "the lawyer paid the client"),
]
UNRELATED = "the kettle boiled quietly on the windowsill"


def load_direct_words() -> tuple[list[str], np.ndarray]:
    """Single-word, DIRECTLY rated surfaces (prov tag present) + their EPA."""
    import lmdb
    env = lmdb.open(EPA_LMDB, readonly=True, max_dbs=8, lock=False)
    epa_db, prov_db = env.open_db(b"epa", create=False), env.open_db(b"prov", create=False)
    S = struct.Struct("<fff")
    direct, out_w, out_v = set(), [], []
    with env.begin() as t:
        for k, _ in t.cursor(db=prov_db):
            direct.add(k.decode("utf-8", "replace")[3:])
        for k, v in t.cursor(db=epa_db):
            s = k.decode("utf-8", "replace")[3:]
            if len(v) == 12 and s in direct and " " not in s and any(c.isalpha() for c in s):
                out_w.append(s)
                out_v.append(S.unpack(v))
    env.close()
    return out_w, np.asarray(out_v, dtype=np.float64)


def cos(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def encoder_sanity(encode) -> tuple[bool, str]:
    """Refuse to emit verdicts from an encoder that carries no signal.

    Without this, P1/P2 compare two cosines that may both be ~0 noise, and a COIN
    FLIP passes: random vectors scored 'negation carried 17/24 (71%)' and
    'permutation is visible'. Ordering tests need a magnitude floor.
    """
    v = encode(["good", "great", "kettle"])
    ident = cos(v[0], v[0])
    rel = cos(v[0], v[1])          # good vs great   -> should be HIGH
    unrel = cos(v[0], v[2])        # good vs kettle  -> should be LOW
    ok = (ident > 0.99) and (rel > 0.40) and (rel - unrel > 0.20)
    msg = (f"identity={ident:.3f}  cos(good,great)={rel:.3f}  "
           f"cos(good,kettle)={unrel:.3f}  margin={rel-unrel:.3f}")
    return ok, msg


def ridge_fit(X, Y, lam=1.0):
    """Closed-form ridge. X:(n,d) Y:(n,3). Returns W:(d+1,3) with bias row."""
    n, d = X.shape
    Xb = np.hstack([X, np.ones((n, 1))])
    A = Xb.T @ Xb + lam * np.eye(d + 1)
    A[-1, -1] -= lam                       # do not regularise the bias
    return np.linalg.solve(A, Xb.T @ Y)


def r2(y_true, y_pred):
    ss_res = ((y_true - y_pred) ** 2).sum(0)
    ss_tot = ((y_true - y_true.mean(0)) ** 2).sum(0)
    return 1.0 - ss_res / np.maximum(ss_tot, 1e-12)


def knn_predict(Xtr, Ytr, Xte, k=10, chunk=512):
    """The CLAUDE_SYSTEM2:124 design: k=10 nearest by cosine, inverse-distance mean."""
    Xtr_n = Xtr / (np.linalg.norm(Xtr, axis=1, keepdims=True) + 1e-12)
    Xte_n = Xte / (np.linalg.norm(Xte, axis=1, keepdims=True) + 1e-12)
    out = np.empty((len(Xte), Ytr.shape[1]))
    for i in range(0, len(Xte), chunk):
        sims = Xte_n[i:i + chunk] @ Xtr_n.T
        idx = np.argpartition(-sims, k, axis=1)[:, :k]
        for r, row in enumerate(idx):
            s = np.clip(sims[r, row], -0.999, 0.999)
            w = 1.0 / (1.0 - s)                     # inverse "distance"
            out[i + r] = (Ytr[row] * w[:, None]).sum(0) / w.sum()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--synthetic", action="store_true",
                    help="random vectors; verifies the harness without a model")
    ap.add_argument("--device", default=None)
    ap.add_argument("--sample", type=int, default=0, help="subsample the fit pool")
    ap.add_argument("--lam", type=float, default=1.0)
    a = ap.parse_args()

    words, Y = load_direct_words()
    if a.sample and a.sample < len(words):
        rng = np.random.default_rng(0)
        sel = rng.choice(len(words), a.sample, replace=False)
        words = [words[i] for i in sel]
        Y = Y[sel]
    print(f"pool: {len(words):,} directly-rated single words\n")

    if a.synthetic:
        rng = np.random.default_rng(0)
        # random vectors carry no information -> R^2 must be ~0. Harness check only.
        emb = rng.normal(size=(len(words), 768))
        def encode(texts):
            return rng.normal(size=(len(texts), 768))
        print("!! SYNTHETIC MODE: random vectors. Expect R^2 ~ 0 and cos ~ 0.\n")
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(MODEL, device=a.device)
        def encode(texts):
            return model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        print(f"embedding {len(words):,} words with {MODEL} ...")
        emb = model.encode(words, batch_size=256, convert_to_numpy=True,
                           show_progress_bar=True)

    # ---- encoder sanity: no signal -> no verdicts --------------------------
    sane, sanity_msg = encoder_sanity(encode)
    print("=" * 72)
    print(f"ENCODER SANITY  {sanity_msg}")
    print(f"  -> {'PASS' if sane else 'FAIL: encoder carries no signal; P1/P2 verdicts SUPPRESSED'}")
    print("=" * 72)

    # ---- P1: negation -----------------------------------------------------
    print("\nP1  NEGATION   how close does the encoder keep 'not w' to 'w'?")
    print("-" * 72)
    wins, A_all = 0, []
    for w, anti in ANTONYMS:
        v = encode([w, f"not {w}", anti])
        A_ = cos(v[0], v[1])          # w  vs  not w   (want LOW)
        B_ = cos(v[1], v[2])          # not w  vs  antonym (want HIGH)
        A_all.append(A_)
        wins += B_ > A_
        print(f"  {w:<10} cos(w,not w)={A_:+.3f}  cos(not w,{anti})={B_:+.3f}")
    rate, mA = wins / len(ANTONYMS), float(np.mean(A_all))
    print(f"\n  mean cos(w, 'not w') = {mA:.3f}   <- PRIMARY statistic")
    print(f"  'not w' nearer antonym in {wins}/{len(ANTONYMS)} pairs ({rate*100:.0f}%)")
    print(f"  mean-composition baseline: cos(w, not w) = 1.000 (identical) in ALL pairs")
    if not sane:
        print("  VERDICT: SUPPRESSED (encoder failed sanity; ordering alone is a coin flip)")
    elif mA >= 0.80:
        print(f"  VERDICT: negation LARGELY LOST (mean {mA:.3f} >= 0.80) -> symbolic "
              "Claim.polarity override REQUIRED")
    elif mA <= 0.50 and rate > 0.5:
        print("  VERDICT: negation CARRIED -> embedding composition handles it")
    else:
        print(f"  VERDICT: negation PARTIAL (mean {mA:.3f}) -> symbolic override "
              "RECOMMENDED; embedding alone insufficient")

    # ---- P2: word order ---------------------------------------------------
    print("\nP2  WORD ORDER   does permutation move the vector?")
    print("-" * 72)
    ds, nulls = [], []
    for s1, s2 in SVO:
        v = encode([s1, s2, UNRELATED])
        d = cos(v[0], v[1]); n = cos(v[0], v[2])
        ds.append(d); nulls.append(n)
        print(f"  cos(perm)={d:+.3f}  cos(unrelated)={n:+.3f}   {s1[:38]}")
    md, mn = float(np.mean(ds)), float(np.mean(nulls))
    print(f"\n  mean cos(permuted)  = {md:.3f}   (mean-composition gives exactly 1.000)")
    print(f"  mean cos(unrelated) = {mn:.3f}   (floor)")
    if not sane:
        print("  VERDICT: SUPPRESSED (encoder failed sanity)")
    elif md >= 0.95:
        print(f"  VERDICT: order BARELY visible (mean {md:.3f}) -- better than 1.000, "
              "but weak")
    elif md > mn + 0.15:
        print("  VERDICT: order VISIBLE -> embedding composition fixes permutation")
    else:
        print("  VERDICT: permutation destroys the vector (cos ~ unrelated); suspect")

    # ---- P3: 768 -> EPA ---------------------------------------------------
    print("\n" + "=" * 72)
    print("P3  IMPUTATION   ridge 768 -> EPA, held-out 20%")
    print("=" * 72)
    rng = np.random.default_rng(0)
    perm = rng.permutation(len(words))
    cut = int(0.8 * len(words))
    tr, te = perm[:cut], perm[cut:]
    Xtr, Xte, Ytr, Yte = emb[tr], emb[te], Y[tr], Y[te]

    W = ridge_fit(Xtr, Ytr, a.lam)
    pred = np.hstack([Xte, np.ones((len(Xte), 1))]) @ W
    r2_ridge = r2(Yte, pred)
    r2_mean = r2(Yte, np.tile(Ytr.mean(0), (len(Yte), 1)))
    r2_knn = r2(Yte, knn_predict(Xtr, Ytr, Xte))

    print(f"  train {len(tr):,}   test {len(te):,}\n")
    print(f"  {'axis':<6}{'ridge':>8}{'kNN(10)':>10}{'mean':>8}{'ceiling':>10}"
          f"{'adopt>=':>10}{'  verdict'}")
    print("  " + "-" * 66)
    for i, ax in enumerate(("E", "P", "A")):
        thr, ceil = ADOPT_THRESHOLD[ax], CEILING[ax]
        best = max(r2_ridge[i], r2_knn[i])
        if ax == "P":
            verdict = "NEVER (ceiling 0.108)"
        else:
            verdict = "ADOPT" if best >= thr else "reject -> leave NULL"
        print(f"  {ax:<6}{r2_ridge[i]:>8.3f}{r2_knn[i]:>10.3f}{r2_mean[i]:>8.3f}"
              f"{ceil:>10.3f}{thr:>10.3f}  {verdict}")
    print("\n  R^2 is bounded by the ceiling, not by 1.0. Report best/ceiling:")
    for i, ax in enumerate(("E", "P", "A")):
        best = max(r2_ridge[i], r2_knn[i])
        print(f"    {ax}: {best/CEILING[ax]*100:5.1f}% of achievable variance")

    print("\nWrite this outcome into docs/compression/spec-meta-db.md, "
          "whichever way it lands.")


if __name__ == "__main__":
    sys.exit(main())
