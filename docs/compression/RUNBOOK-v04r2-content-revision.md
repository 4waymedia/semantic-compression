# RUNBOOK — publishing `elo-browser-v04r2` as a content revision

> The first use of the revision path: v04's id space is kept, its facets are corrected,
> and `wordclass` starts shipping. No rebuild, no renumbering, no `.elo` invalidated.

## Why a revision and not a v05

The two things changing are **asset content** (14,394 facet records get the correct
UTILITY) and **asset set** (`wordclass` starts shipping). Neither is in the id space.
`forward`/`reverse` are untouched, so every id in every fixture and every stored `.elo`
still resolves to the same surface. A new build name would pay the renumbering cost for a
problem that is not there.

**`package_revision` means the assets INCLUDING their content.** Bump it on add *or*
rebuild. That definition is what makes v04r1 (bad facets, no wordclass) and v04r2 (fixed
facets, wordclass shipping) distinguishable.

## Run it

PowerShell, from the repo root. Chain with `;` not `&&`.

```powershell
cd F:\Script-Projects\elo-dev\elo_dev\R-D-concepts\semantic_compression
$env:PYTHONPATH="."

# 0. What is this build missing? Reads only.
python build_assets.py db/builds/elo-browser-v04 --audit

# 1. Record the content change. Bumps package_revision 1 -> 2 and logs the reason.
python build_assets.py db/builds/elo-browser-v04 `
    --bump-revision "facets UTILITY reads non-lexical provenance: 14,394 CSS/HTML/JS surfaces CONTENT -> STRUCTURAL" `
    --only 1 --dry-run
# drop --dry-run when the plan looks right; --only 1 re-derives facets alone

# 2. Widen the suite to declare wordclass, then build + export it (stages 15, 16).
python build_assets.py db/builds/elo-browser-v04 --add-asset wordclass --dry-run
# drop --dry-run to run. Stage 15 needs Resources/books; it FAILS rather than
# degrading to the 427k sample.

# 3. Re-derive the registry + re-verify (stage 9 always runs; 11/12 are gates).
python build_assets.py db/builds/elo-browser-v04 --only 9,11,12

# 4. Publish. Lands in dist/dictionary/elo-browser-v04r2/.
python publish_dictionary.py db/builds/elo-browser-v04 --dry-run
python publish_dictionary.py db/builds/elo-browser-v04

# 5. Verify the published bundle, both directions.
python publish_dictionary.py --verify ../dist/dictionary/elo-browser-v04r2
```

## What each step asserts

| step | the check that can fail |
|---|---|
| 1, 2 | **id space unchanged** — `forward` re-fingerprinted after the run; a move aborts with "this is a new dictionary and needs a new BUILD NAME, not a package_revision" |
| 2 | the **orphan check** — a registry asset with no builder stage aborts the cascade |
| 4 | **G9** every payload file declared · **G10** every built asset ships · **G11** content moved ⇒ revision moved · plus G1–G8 |
| 5 | two-way: declared files intact **and** no undeclared file present |

## The three identities, after this

| identity | value here | who pins it |
|---|---|---|
| `dictionary_fingerprint` | `b0164e50816af845…` — unchanged | **`.elo` files.** Conversion between dictionaries keys on THIS, not the build name |
| `build` | `elo-browser-v04` — unchanged | the id space; fixtures keyed to ids stay valid |
| `bundle_id` | **`elo-browser-v04r2`** | **consumers.** ELO-Browser's `FIXTURE_BUILD` tripwire compares this string |

**Consumers must pin `bundle_id`, not `build`.** A revision that lived only as an integer
inside `BUNDLE.json` would not fire the browser's tripwire — it compares names — so the
facets change would have landed silently, which is the exact staleness the revision
exists to prevent. Putting the revision in the pinned string is what makes it work.

## Expected results

```
facets   UTILITY  CONTENT 99.85% -> 96.57%   effective cardinality 1.012 -> 1.169
         14,394 CONTENT -> STRUCTURAL, 40 bucket CONCEPT -> TOPIC, id space unchanged
wordclass wordclass.bin 1.31 MB, 437,995 records + wordclass.names.json
bundle   5 channels: facets, epa, vfacets, neighbours, wordclass
```

`neighbours` will still be the revision-1 index — it is built from the denotative index,
which selects on `utility='CONTENT'`, so the 7,209 web-structure embeddings only clear on
a re-run of stages 5 and 8. **That is a further content change and needs its own
revision** (`--bump-revision "denotative index rebuilt after the utility fix"` then
`--only 5,8`). Doing it in the same revision would publish a bundle whose facets say
STRUCTURAL and whose neighbours were chosen as if they were CONTENT — internally
inconsistent, and G11 cannot catch that because both would be revision 2.

Deferred deliberately: stage 5 embeds ~262k surfaces on the GPU and is the long pole.
v04r2 is honest without it as long as this paragraph ships with it.
