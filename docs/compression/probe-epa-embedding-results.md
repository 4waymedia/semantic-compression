# EPA × Embedding — Probe Results (measured 2026-07-10)

> The measurement that decided EPA channel 2's fate. Decision rules were
> **pre-registered** in `probe_epa_embedding.py` before the numbers existed.
> Encoder: `all-mpnet-base-v2` (768-d), CPU, 44,811 directly-rated single words.
> Reproduce: `python probe_epa_embedding.py --device cpu`
> Control:   `python probe_epa_embedding.py --synthetic` (must SUPPRESS all verdicts)

---

## 0. Why the probe existed

`epa_match._compose` builds phrase EPA as an **elementwise mean over RATED
constituents**, silently dropping unrated tokens (`epa_match.py:149`):

```python
rated = [v for v in (_lookup(w, norms)[0] for w in words) if v is not None]
```

Measured consequences on the live substrate:

```
good          -> (2.890, 1.410, -1.340)
not good      -> (2.890, 1.410, -1.340)      IDENTICAL   ('not' is unrated -> dropped)
a bad day     -> (-0.200, -0.425, -0.760)
not a bad day -> (-0.200, -0.425, -0.760)    IDENTICAL
dog bites man -> (1.210, 0.585, -0.105)
man bites dog -> (1.210, 0.585, -0.105)      IDENTICAL   (mean is permutation-invariant)
```

Scale: **69.9%** of filled EPA in `general_v0.4_char4` (163,749 / 234,286) is
mean-composed. **43.9%** of the vocabulary (183,555 / 417,841) has no EPA at all.

The proposal under test: replace mean-composition with a projection from the
surface's own 768-d embedding, and impute EPA for unrated surfaces the same way.

---

## 1. Encoder sanity (gate — no signal, no verdict)

```
identity = 1.000   cos(good, great) = 0.742   cos(good, kettle) = 0.179   margin = 0.563
-> PASS
```

> **Why this gate exists.** The first draft of P1/P2 compared only the *ordering* of
> two cosines. On random vectors that is a coin flip: the probe reported *"negation
> carried in 17/24 pairs (71%) -> mpnet carries negation"* **from pure noise**. A
> second seed gave 11/24 (46%). The gate now requires the encoder to demonstrate
> signal before any verdict is emitted, and P1/P2 judge **magnitudes**, not orderings.
> The synthetic control must print `SUPPRESSED`. If it does not, the probe is broken
> and the real numbers are worthless.

---

## 2. P1 — Negation: **mostly lost** (mean 0.720)

`mean cos(w, "not w") = 0.720`. *"not w"* is nearer its antonym in only **3 / 24** pairs.

| w | cos(w, "not w") | cos("not w", antonym) |
|---|---:|---:|
| good | 0.432 | 0.587 |
| safe | 0.777 | 0.617 |
| calm | 0.838 | 0.563 |
| polite | 0.846 | 0.709 |
| early | 0.862 | 0.613 |
| full | 0.830 | 0.503 |

Baseline for comparison: **mean-composition scores cos = 1.000 (identical) in ALL pairs.**

**Verdict: negation PARTIAL — embedding alone insufficient.** mpnet registers that
*something* changed and does not register that it was the truth value.

---

## 3. P2 — Word order: **effectively invisible** (mean 0.973)

```
mean cos(svo, permuted)   = 0.973      (mean-composition gives exactly 1.000)
mean cos(svo, unrelated)  = 0.042      (floor)
reference: cos(good, great) = 0.742
```

Read that carefully: **mpnet rates a sentence and its agent/patient inversion as more
similar to each other (0.973) than two actual synonyms are (0.742).** The vector moves
0.027 while the meaning inverts completely.

**Verdict: order barely visible. Embedding composition does NOT fix word order.**

### 3a. The reframe (this corrected an error in our own reasoning)

Permutation-invariance is a **semantic** defect, not an **affective** one. Ask what
EPA is for: does `dog bites man` *feel* different from `man bites dog`? Barely — both
are mildly negative, moderately potent, active. **The affect of a bag of words is
close to genuinely permutation-invariant.**

Negation is not. `not good` must carry the **opposite sign** on E. That is an affect
error.

> We had framed word-order erasure as the deep problem and negation as its symptom.
> It is backwards. **Mean-composition's only serious sin is negation.**

---

## 4. P3 — Imputation: E and A adopt; P does not

Ridge (768 -> 3) and kNN(k=10, inverse-distance), 80/20 held out, n = 44,811.
Train 35,848 · test 8,963.

| axis | ridge R² | kNN R² | predict-mean | R² ceiling | adopt ≥ | verdict |
|---|---:|---:|---:|---:|---:|---|
| **E** | 0.508 | 0.509 | −0.000 | 0.663 | 0.431 | **ADOPT** (76.8% of achievable) |
| **A** | 0.315 | 0.310 | −0.000 | 0.376 | 0.244 | **ADOPT** (83.7% of achievable) |
| **P** | 0.360 | 0.367 | −0.000 | 0.108 | — | **NEVER** — see §5 |

**kNN ≈ ridge on every axis.** So the k-NN imputation machinery specified in
`CLAUDE_SYSTEM2.md:122-124` is unnecessary: a **768×3 linear map (9 KB)** does the same
job, deterministically, with no index at inference, and applies to any embeddable text.
That design is superseded.

---

## 5. The P anomaly — a mis-specified ceiling (our error, recorded)

The probe printed `P: 340.1% of achievable variance`. **R² cannot exceed a noise
ceiling.** The ceiling was wrong, not the model.

The cross-source `r = 0.328` measures *Warriner-P vs NRC-P*. But the substrate's P is
**not a consensus of the two** — `manifest` gives Warriner priority 80, so Warriner
wins where present and NRC fills the rest. The regression target is therefore
*whichever source supplied that word*, not a noisy average of raters. Within a single
source P is internally consistent, and an embedding learns it — including its
idiosyncrasies.

So `R²(P) = 0.36` means **"we can reproduce Warriner's dominance ratings."** It does
not mean we can measure potency.

> ### The distinction that governs this decision
> **R² measures LEARNABILITY, not VALIDITY.**
> * E is learnable (0.508) **and** valid (cross-source r = 0.814).
> * P is learnable (0.36) and **not** valid (cross-source r = 0.328).
>
> Imputation propagates learnability. It cannot manufacture validity. Faithfully
> reproducing a measurement two human panels disagree about is not measurement.

**P stays NULL.** The pre-registration is honored — not because the number was low
(it wasn't), but because the number never licensed the claim.

---

## 6. Decisions (binding)

| # | Decision | Basis |
|---|---|---|
| D1 | **Do NOT rebuild phrase EPA on embeddings.** | P2 = 0.973 (order-blind) and P1 = 0.720 (negation-blind). It buys ~nothing over the mean. |
| D2 | **Fix negation symbolically.** Phrase EPA must consult the deterministic `Claim.polarity` (affirmed \| negated), which is tested 11/11 and already flows through `MemorySeed`. | P1 — no encoder carries negation reliably. |
| D3 | **Impute E and A** via a 768×3 ridge map — **conditional on §7.** | P3 — 77% / 84% of achievable variance. |
| D4 | **Never impute P.** Leave NULL (O-EPA9: never fake-neutral). | §5 — learnable, not valid. |
| D5 | **Ridge supersedes kNN** for imputation. | kNN ≈ ridge; ridge is 9 KB and index-free. |

---

## 7. Open condition before D3 ships — distribution shift

The held-out 8,963 words are drawn from the **rated** pool. Warriner and NRC rate
**common** words. The imputation target is the 183,555 **unrated** surfaces, which are
disproportionately **rare and technical**.

`R²(E) = 0.508` is established on common words and is **unmeasured where it would
actually be used.**

**Required check:** stratify the held-out set by corpus frequency; report R² in the
lowest-frequency decile. If E holds there, impute. If it collapses, the 43.9% stays
NULL and EPA remains a small, honestly-gated affect channel.

Any imputed value MUST carry its own `prov` tag. Predicted affect must never be
indistinguishable from a human rating.

---

## 8. Provenance of the numbers

- Substrate: `Memory/data/epa_substrate.lmdb`, 67,936 entries, `sources = [warriner, nrcvad_v2.1]`.
- Cross-source agreement (`meta.v1_matrix`, 13,812-word overlap): **E 0.814 · A 0.613 · P 0.328**.
- Invisible negators (unrated → dropped by `_compose`): `not`, `no`, `nor`, `n't`.
  Rated (survive composition): `never`, `none`, `cannot`, `without`, `nothing`, `nobody`, `neither`, `dont`, `cant`.
- Substrate phrases containing an invisible negator: **142 / 23,125 (0.61%)**.
  (The "every negated phrase" framing was **refuted**; the 69.9% figure is the
  *composition rate*, not the corruption rate. Severity ≠ frequency: `not good == good`
  is a sign flip, not a rounding error.)
