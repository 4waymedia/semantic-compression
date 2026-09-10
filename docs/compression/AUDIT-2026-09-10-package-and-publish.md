# AUDIT — the ELO Dictionary package and its publish path — 2026-09-10

> **Scope:** every asset the dictionary builds, every step that publishes one, and what
> the `elo-dictionary` package actually hands a consumer.
> **Method:** measured against `elo-browser-v04` and `elo-browser-v01c` on disk. Every
> number below is reproducible from committed artifacts. Nothing here is inferred from a
> docstring — the previous two errors in this lane both came from that.

## 0. What the artifact is (recorded, because the name misleads)

The ELO Dictionary is **an id↔asset binding table**, not a word list. One id space —
Base64, tier by length — covers words, phrases, private file ids, CSS, JavaScript, HTML,
and reserved system ids. The value is not that a surface has an id; it is that **the id
resolves into every asset the build carries**: `epa`, `facets`, `vfacets`, `neighbours`,
`wordclass`, and whatever comes next.

Two consequences govern everything below:

1. **An asset is only worth having if it ships and can be read.** An asset in the build's
   LMDB that never reaches a consumer is not an asset, it is a local file.
2. **Assets accumulate.** Older dictionaries must be re-buildable with newly added
   assets. That means the pipeline has to be able to answer *"which assets is this build
   missing?"* — not just *"run everything again."*

The audit is organised around those two.

---

## 1. Asset inventory — measured, `elo-browser-v04`

| asset | in LMDB | entries | ships in bundle | decode contract | hashed | in manifest.json |
|---|---|---:|---|---|---|---|
| `forward` | yes | 437,995 | via `.browser.json` | — | yes | yes (`dictionary`) |
| `reverse` | yes | 437,995 | via `.browser.json` | — | yes | yes (`dictionary`) |
| `facets` | yes | 437,995 | `facets.bin` | `facets.names.json` | yes | yes |
| `epa` | yes | 236,645 | `epa.bin` | **none** | yes | yes |
| `vfacets` | yes | 437,995 | `vfacets.bin` | **not in `dist/`** | yes | yes |
| `neighbours` | no (CSR only) | 258,254 | `neighbours.bin` | **none** | yes | **no** |
| **`wordclass`** | yes | **437,995** | **NO** | **none** | **no** | **NO** |
| `templates` | yes | **0** | no | none | n/a | yes (`present=false`) |

### 1.1 FINDING — `wordclass` is built and never leaves the building

437,995 records. Format v3, locked. Stamped in `meta` as `wordclass_format_version: 3`.
Stage 15 of the cascade. Adopted by the Verbalizer lane. **And it has no exporter, no
`.bin`, no wire magic, no names contract, no entry in `manifest.json`, and it is not a
`BUNDLE_CHANNEL`.**

Anyone consuming the published bundle cannot get wordclass at all. The Verbalizer has it
only because it opens the build's LMDB directly — the exact coupling the package exists
to remove.

This is the single largest gap in the package.

### 1.2 FINDING — `templates` is the mirror image

Declared in `artifact_identity.KINDS`, carried in `manifest.json` as an artifact with
`present: false`, and **has no builder stage anywhere**. `dictionary_info` meanwhile
reports it under `unknown_subdbs`, so two registries in the same repo disagree about
whether it is a known asset.

Built-and-undeclared (`wordclass`) and declared-and-unbuilt (`templates`) are the same
defect from opposite ends: **there is no single list of what an asset is.**

### 1.3 FINDING — decode contracts are shipped inconsistently

`facets.bin` ships with `facets.names.json`. `vfacets.bin`, `epa.bin` and
`neighbours.bin` ship with nothing, and `vfacets.names.json` exists but was never copied
into `dist/` (fixed today; the next publish carries it).

A channel without its geometry is undecodable from the bundle alone. `epa` is arguably
self-describing (`<fff`), `neighbours` is emphatically not (CSR, `k`, `min_sim`, an 80-byte
header whose `fp_len` field this lane has now misread twice).

---

## 2. The seven registries

Adding an asset requires editing **seven independent hand-maintained lists**, none of
which is checked against another:

| # | file | list | knows `wordclass`? |
|---|---|---|---|
| 1 | `build_assets.py` | `_stages()` | yes (stage 15) |
| 2 | `publish_dictionary.py` | `BUNDLE_CHANNELS` / `OPTIONAL_CHANNELS` | **no** |
| 3 | `publish_dictionary.py` | `MAGIC` (wire headers) | **no** |
| 4 | `export_browser_assets.py` | the exporter body | **no** |
| 5 | `artifact_identity.py` | `KINDS` | **no** |
| 6 | `coverage_census.py` | `CHANNELS` | yes |
| 7 | `compression_dictionary/config.py` | `KNOWN_SUBDBS` | yes |

Plus `api.py`'s `_field_readers` and `coverage()` (both know it).

**Three of seven know about an asset that has existed for a week and is locked.** The
publish path is exactly the three that do not.

This is the same root cause as this morning's manifest defect, one level up: that was
five hand-maintained *file* lists, this is seven hand-maintained *asset* lists. The fix
there was to derive from the directory. The fix here is to derive from one registry.

---

## 3. Publish path — what is gated and what is not

Gates on a v04 dry-run, all PASS: G1 one vocabulary/one n · G2 fingerprints agree ·
G3 manifest vs reality · G4 no build-time artifact · **G9 every payload file declared
(new today)** · G7 coverage recorded · G8 end-to-end spot read · G5 round-trip ·
G6 facets.

**What no gate asks:**

- *Does the bundle carry every asset the build has?* G3 checks the manifest's
  present-flags against the shipped channels — but only for channels in
  `BUNDLE_CHANNELS`, so an asset missing from that tuple is invisible to it. **`wordclass`
  passes every gate by not being mentioned.**
- *Does every shipped channel have a decode contract?* Nothing checks.
- *Is the bundle's asset set the same as the previous publish's, or deliberately
  different?* Nothing checks; there is no diff between publications.

G9 closed "a file that ships is declared." The open half is **"an asset that exists
ships."**

---

## 4. Rebuilding an older dictionary with newer assets

Paul's requirement, checked directly.

**The machinery exists and works.** `build_assets.py` has `--only`, `--from`, `--force`,
and a per-stage ledger keyed on `(dict_sig, script_mtime, argv_sig)`, so re-running one
stage against an older package is a supported operation.

**What is missing is the question that comes first.** Measured:

```
elo-browser-v01c   sub-dbs: epa facets forward meta reverse vfacets
elo-browser-v04    sub-dbs: epa facets forward meta reverse vfacets wordclass templates
v01c ledger        stages 1-12   (no 13 vfacets, no 14 census, no 15 wordclass)
```

v01c's LMDB **has** `vfacets` while its ledger has no vfacets stage — the channel was
patched in by hand, which is documented in `build_assets.py` and is precisely the hazard
the stage was created to end. v01c has no `wordclass` and nothing announces that.

`dictionary_info()` is the right instrument and already answers most of it — it reports
`wordclass: present=false` for v01c and flags `templates` as unknown. **It is not wired
to anything.** No stage, gate, or CLI compares a build's assets against the current asset
set and says *"this build is two assets behind; run `--only 13,15`."*

That verb is the concrete form of Paul's requirement, and it does not exist.

---

## 5. The package — is it a usable script suite?

`elo-dictionary` 0.3.1. Seventeen modules. Measured:

**What works well.** `open_dictionary()` and its verbs (`text`, `words`, `surfaces`,
`ids_of`, `epa`, `facets`, `vfacets`, `wordclass`, `assets`, `explain_field`,
`coverage`), the resolver, `caps_codec`, `tokenizer`, `dictionary_info`, and the config
contract (`PRIMITIVES`, `STRUCTURAL_IDS`, `TIER_CAPACITY`). A consumer can open the
standard and read every channel without hand-decoding bits. That part of Paul's
"usable script suite" is real and is the work of the last two weeks.

**Three gaps:**

**5.1 — No CLI. No entry points at all.** `pyproject.toml` declares no
`[project.scripts]`. There is no `elo-dict` command; every operation requires writing
Python and knowing which module to import. For a package whose job is to be *engaged*,
this is the most visible absence.

**5.2 — The verbs are importable but not public.** Measured:

```
open_dictionary        attr=True   in __all__=False
Dictionary             attr=True   in __all__=False
resolve_dictionary     attr=True   in __all__=False
ChannelUnavailable     attr=True   in __all__=False
```

`from compression_dictionary import *` does not get the verbs, and any tool reading
`__all__` as the public surface reports the entire API private. The 09-08 work shipped
the door and left it off the map.

**5.3 — The package cannot build, publish, or verify.** Absent from the package:
`facet_builder`, `epa_match`, `vfacet_builder`, `wordclass_builder`, `build_assets`,
`build_from_spec`, `publish_dictionary`, `verify_facets`, `verify_lossless`,
`coverage_census`, `artifact_identity`, `dictionary_standard`.

It ships `dictionary_builder_v03`, `library_builder`, `phrase_miner`, `ngram_counter`,
`corpus_scanner`, `word_frequency_counter` — the *core* build — and none of the **asset**
builders or any publish tooling.

So the package can consume a dictionary and cannot produce or check one. **Paul's
"older dictionaries can be rebuilt with them" is not possible from the package**; it
requires the repo.

---

## 5a. ADDENDUM — the count was seven, and it was nine

After writing §2 I found two more, and the second one matters most:

| # | file | list | knew `wordclass`? |
|---|---|---|---|
| 8 | `build_suite.ORDER` / `DEPS` / `IDENTITY_KIND` | asset order + deps | **no** |
| 9 | `build_suite.PRESETS["full"]` | what a `full` build enables | **no** |

**Cascade stage 15 — created specifically to stop `wordclass` being hand-built — has
never executed.** `PRESETS["full"]` did not contain `wordclass`, so every build reported
`15 wordclass  off (not in suite)  skip`. The stage existed, was correct, and was inert.
That is the most expensive shape of this bug: a fix that looks landed. The channel in
elo-browser-v04 is still the hand-built one described in the stage's own comment.

Nine lists. Seven did not know about the asset.

---

## 6. What I propose, in order

1. **One asset registry.** A single declarative table — name, sub-db, record width, wire
   magic, builder stage, exporter, contract file, absent-marker — that stages 1–15, the
   exporter, `publish_dictionary`, `artifact_identity`, `coverage_census` and the API all
   read. Replaces seven lists with one. This is the fix that stops the class.
2. **Ship `wordclass`.** Wire format + `wordclass.names.json` + exporter stage + bundle
   channel. It is locked, adopted, and 100% populated; it is the asset most obviously
   owed to other lanes.
3. **Gate G10 — every asset the build carries is either shipped or explicitly excluded**,
   with the exclusion named in the registry. Turns §3's blind spot into a refusal.
4. **Contract file per channel.** `epa.names.json`, `neighbours.names.json`, and
   `vfacets.names.json` in `dist/`. A channel ships with its geometry or it does not ship.
5. **`build_assets.py --audit <build>`** — diff a build's assets against the registry and
   print the `--only` line that brings it current. Paul's rebuild requirement, as a verb.
6. **Package surface:** `[project.scripts]` with an `elo-dict` CLI (`info`, `resolve`,
   `explain`, `coverage`, `verify`), and `__all__` corrected so the verbs are public.
7. **Decide** whether the asset builders belong in the package or stay repo-only. If
   older builds are to be upgraded by consumers rather than by us, they must ship.

Items 2, 4 and 6 are independent and small. Item 1 is the one that matters and it touches
seven modules, including a documented CLI stage contract.

---

## 7. Errors this lane made during the audit period, recorded

- **Bounded `find` read as absence** → attributed the browser's manifest gap to the
  published bundle, in a handoff four lanes acted on. `dist/` was outside the search root.
- **`fp_len` read as a header length** → manufactured a 64-edge discrepancy in a correct
  `neighbours.bin`. Second time on an 80-byte header.
- **`.strip()` on the non-lexical provenance file** → would have shipped 93 ordinary
  English words as STRUCTURAL, while improving every summary statistic.

All three: a property of an artifact inferred without consulting what produces it. The
rule, stated positively — **ask the writer** — belongs in `LANES.md`, and the integration
lane is folding four independent arrivals at it into one entry.

**Still no gold set. Every number in this audit is coverage, population, or presence.
None of it is accuracy.**
