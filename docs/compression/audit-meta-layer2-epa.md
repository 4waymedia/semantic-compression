# Audit — System-2 Meta-Facets (L7), EPA + Polarity Slice

Date: 2026-07-04. Scope: pre-build audit for filling `epa_e/p/a` + `polarity`
(meta_layer 1 → 2) per `spec-meta-db.md` §5. Method: read the real code, run
measurements against `general_v0.4_char4/meta.db` (the N1 rebuild). Claims
tagged per the CLAUDE.md validation loop.

---

## 1. Spec §5 claims vs the code

| Claim | Status | Evidence |
|---|---|---|
| Projector exists at `Memory/mneme/substrate/epa_projector.py` with `build_epa_db` + `EPAProjector.get_with_source` | **VALIDATED** | Read 2026-07-04; API matches |
| Warriner 13,915 lemmas; E=valence−5, P=dominance−5, A=arousal−5; `<fff>` 12 B LE | **VALIDATED** | `load_warriner`, `EPA_STRUCT`; CSV = 13,915 rows at `Memory/data/warriner_2013_norms.csv` |
| difflib fallback cutoff 0.72, best of 3, neutral (0,0,0) if none | **VALIDATED** | `FALLBACK_CUTOFF/N/NEUTRAL` constants |
| S2 columns null, `meta_layer=1` in current build | **VALIDATED** | `meta_info`: layer 1, 417,841 rows; `S2_RESERVED` inserted as NULL by `meta_builder.py` |
| O-EPA1 keying divergence (surface bytes vs id_bytes) | **VALIDATED** | `b'epa'` keyed by token UTF-8 in *mneme's* `dictionary.lmdb` — a different artifact from the compression build package. Two stores named `dictionary.lmdb` exist; the projector never touches the build package. |
| O-EPA2 no version stamp | **VALIDATED** | No fingerprint/version anywhere in `epa_projector.py` |
| EPA fill will fix the abstraction gate (0.28 FAIL) | **UNVERIFIED** | N1 tags it "the System-2/EPA gap" but no measurement yet links EPA columns to the gold-set abstraction judgment. Verify on the 71-row gold set before claiming. |

Minor spec↔code divergence: `spec-meta-db.md` §3 lists a `frequency` column;
`meta_builder.COLS` does not include it. Coverage below is therefore
surface-weighted, not token-weighted.

## 2. Warriner coverage over the 417,841 surfaces (measured 2026-07-04)

Words: 250,447 · Phrases: 167,394 (40.1% of rows).

| Segment | Direct Warriner hit |
|---|---:|
| All word surfaces | 5.4% (13,594) |
| Tier 0 (52) | 7.7% |
| Tier 1 (1,024) | **52.4%** |
| Tier 2 (11,730) | **45.3%** |
| Tier 3 (237,641) | 3.3% |

- Exact suffix-strip recovers a further 6.8% of words; **87.8% would go to
  difflib fallback.**
- Fallback sample (n=200 random misses): **70% get a match, 30% neutral.**
  Quality is bimodal — morphology is good (`scrapes→scrape`,
  `maintainable→maintain`, `coffe→coffee`) but proper nouns and rare words get
  confidently wrong EPA (`savitri→savior`, `pritchett→ratchet`,
  `palsied→allied`).
- Full fallback pass ≈ 0.6 h single-thread (10 ms/token) — feasible per build.

Reading: the headline 5.4% is dominated by the tier-3 long tail (names, typos,
ASR junk). Token-weighted coverage in real streams will be far higher since
tier 1–2 dominate occurrences — but this can't be computed until `frequency`
is in the meta DB.

## 3. Gaps §5 does not cover (design inputs for the spec/build step)

1. **Phrases (40% of rows).** The EPA contract is token-only. Decide: null
   (defer), head-word EPA, or mean-of-content-words. Recommend explicit null +
   `_method=deferred` in the first slice.
2. **Fallback noise on names.** Don't run difflib blind. Gate by existing
   facets (e.g. only `utility=CONTENT`, skip `AMBIGUOUS`/likely proper nouns),
   and record `_method` (`warriner|stem|difflib|neutral`) + `confidence` per
   row — the columns already exist in the schema for exactly this.
3. **Lemmatization.** Projector docstring: "pass lemmatized tokens." Meta
   surfaces are raw normalized surfaces. The stem-strip result (+6.8%) shows a
   cheap deterministic lemma pass is worth doing before difflib.
4. **Add `frequency` to meta.db** (spec already lists it) so coverage and gate
   impact can be token-weighted.
5. **O-EPA1/O-EPA2 deferred by scope decision** (2026-07-04): keying
   reconciliation and projector version-stamping are out of this slice, but the
   layer-2 pass should read the Warriner CSV directly (deterministic, like the
   projector) rather than binding to mneme's LMDB, sidestepping the
   two-artifact divergence for now.

## 4. Recommended slice (build next)

Layer-2 pass in `meta_builder.py` (or a sibling `meta_layer2.py`):
words only; lookup order direct → stem-strip → difflib(gated) → null;
fill `epa_e/p/a` + `polarity` (bin of E per §5) + `_method` + `confidence`;
set `meta_layer=2`, bump `meta_format_version`, compute the separate S2
fingerprint (S1 deterministic fingerprint unchanged). Then re-run the facet
accuracy gates and measure whether abstraction moves off 0.28 — that number is
the go/no-go for the rest of L7.

---

## ADDENDUM (2026-07-04, same day) — §1/§3/§4 partially superseded

Further reading (`SYSTEM1.md` 2026-06-22/25 sessions, `Memory/mneme/docs/EPA.md`
§5) shows the mneme EPA track already resolved several items this audit treated
as open. Corrections:

- **difflib fallback is REJECTED, not "gate it"** — O-EPA8 measured it at 63%
  valence-sign agreement, MAE ≈ 1.6/±4 (careful→careless class of error);
  O-EPA5 rejected materializing it. Today's independent sample (70% match rate,
  wrong on proper nouns) *confirms* the rejection. §4's "difflib(gated)" step
  is dead — do not build it.
- **O-EPA1 and O-EPA2 are RESOLVED** (two-layer key design; substrate carries
  `global_epa_version` + fingerprint). §1's rows for them describe the old
  projector, not the current substrate.
- **Phrase EPA exists** (O-EPA4): composition + NRC-VAD MWEs; §3.1's "phrases
  undefined" is stale as a design gap — it remains only as a wiring task.
- **The layer-2 source is `Memory/data/epa_substrate.lmdb`** (Warriner +
  NRC-VAD v2.1 union, 67,936 entries, built 2026-06-22, version `c66eeffa62eb`,
  keys `en|<surface>`), NOT the raw Warriner CSV. Measured today against
  `general_v0.4_char4/meta.db`: words 13.5% (tier 1 **80.5%**, tier 2 **63.6%**,
  tier 3 10.7%), phrases 9.5%, CONTENT rows 11.9% (49,604).
- The SYSTEM1.md-claimed "content coverage 56.7% / phrase 97.8%" numbers do not
  match today's surface-level measurement against the char4 build —
  **UNVERIFIED which denominator/build they used**; reconcile before citing
  either number as the coverage of record.

The corrected slice: wire `meta_builder` (layer-2 pass) to `epa_substrate.lmdb`
lookups (direct + lemma only, no difflib), fill `epa_*` + `polarity` +
`_method` + `confidence`, record `global_epa_version` in `meta_info`, flip
`meta_layer=2`, S2 fingerprint. Then re-run the gates.
