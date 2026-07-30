# Dictionary Slot Allocation — Spec (draft v0)

> **The build mechanic for deciding WHERE a surface lands, not just whether it's included.**
> Today a dictionary is one frequency-ranked list with a single hardcoded exception
> (`tier1_word_reserve`). This spec generalizes that into declarative slot allocation, so a
> build spec can state what the dictionary is *for* — and prove it did what it said.
> Status: design. Nothing built. Written 2026-07-30 after the codec character-coverage audit.

---

## 1. Why — the audit made the cost visible

`elo-browser-v01a` shipped without `|`, `` ` ``, `~`, `\r`, `\xa0`, and with `{ } \ \t`
ranked outside every profile cut. Two consequences, one fatal and one expensive:

- **Correctness.** `|` frames the stream (`ELO|1|txt|tok|tok`). Missing from the cut it fell
  to `encode_oov` → `OOV::|` — a token containing a raw delimiter — and decode shattered
  the frame (`unknown stream token:` with an empty token).
- **Cost.** Every missing surface encodes as `OOV:A:x` = **7 stream characters**, against
  **2** for a Tier-1 id. On an HTML-dense page (`< > / = " |`) that is a 3.5× penalty
  applied thousands of times — the likely cause of *negative* compression on captured
  search-results pages.

Both are placement failures, not inclusion failures. `force_include` (already implemented)
fixes inclusion. It cannot express *"HTML structure deserves 2-char ids because web capture
is a first-class use case."* That sentence is what a build spec should be able to say.

---

## 2. The single-ordering principle (the fact that shapes everything)

Measured from `config.py` + `dictionary_builder_v03.py`: the builder produces **one ordered
list**, and ids are assigned sequentially from it. That one ordering determines **both**
outputs:

```
rank ──┬──▶ ID LENGTH (tier)      rank 1..1,280      -> 2-char  (tier 1)
       │                          1,281..83,200      -> 3-char  (tier 2)
       │                          83,201..           -> 4-char  (tier 3)
       │
       └──▶ PROFILE-CUT MEMBERSHIP   tiny      < 32,496
                                      compact   < 65,264
                                      standard  < 130,800
                                      full      < 261,872
                                      reference < 373,917
```

**Therefore: a slot is a RANK RANGE.** "Tier" and "which profiles contain it" are both
*derived* from rank; they are not independent dials. Allocating slots = reserving regions of
the single ordering.

Two corollaries the design must respect:

- **To guarantee a surface exists in *every* profile (including `tiny`), its rank must be
  below 32,496.** A "structural floor" is precisely a low-rank reservation.
- **Cheap ids are scarce and already spent.** Capacity vs v01a usage:

| tier | id len | capacity | v01a used | free |
|---|---|---:|---:|---:|
| 0 | 1 | 64 | 53 | 11 |
| 1 | 2 | 1,280 | **1,280** | **0** |
| 2 | 3 | 81,920 | **81,920** | **0** |
| 3 | 4 | 5,242,880 | 354,737 | 4,888,143 |

**Tier 1 and Tier 2 are completely full.** Of Tier 1's 1,280, `tier1_word_reserve` claimed
1,024, leaving 256 to the frequency pool. So slotting HTML into Tier 1 **displaces**
something — this is a zero-sum allocation, and the spec must make the trade explicit rather
than let it happen silently. Tier 3 has ~4.9M free slots; the scarcity is entirely at
Tier 1/2 and at the cut boundaries.

---

## 3. The slot grammar

```yaml
build:
  slots:
    # Each entry reserves a contiguous rank range, in declaration order, starting after
    # Tier 0. `reserve` is a count of ranks; placement within the range is by the group's
    # own internal frequency (or by list order for explicit `surfaces`).
    - group: structural          # the codec's grammar — never optional
      reserve: 64
      surfaces: ["|", "`", "~", "\r", "\t", "\\", "{", "}", "<", ">", "=", "\"", "/", "#", "*", "+"]
      required: true             # build FAILS if any surface cannot be placed

    - group: html                # what makes web capture pay
      reserve: 192
      source: data/web_terms_frequencies.txt
      top: 192                  # highest-frequency 192 within the group

    - group: words               # today's tier1_word_reserve, now just a group
      reserve: 1024
      source: corpus             # the merged word pool

    # Unreserved ranks fall through to the frequency-ranked open pool (current behaviour).
```

**Semantics**
- Ranges are assigned in **declaration order** — the spec reads top-to-bottom as
  cheapest-to-dearest. This makes the ordering auditable by reading the spec.
- `reserve` is a **count**, not an explicit range. The builder computes offsets, so inserting
  a group doesn't require renumbering the rest by hand.
- A group may declare `surfaces` (explicit), `source` + `top` (frequency within a corpus
  file), or `source: corpus` (the merged pool).
- `required: true` means a placement failure is a **build error**, not a warning.

---

## 4. Validation — the rules that make this safe

Checked at spec load, before any LMDB is written. Every failure names the arithmetic.

1. **No overflow.** `sum(reserve) ≤ capacity` for the ranks consumed. Tier 1 is 1,280;
   `64 + 192 + 1024 = 1,280` exactly — a spec asking for one more must fail, not silently
   spill a group into 3-char ids.
2. **No overlap / no duplicate surface.** A surface appearing in two groups is an error;
   precedence must be resolved by the author, not by chance.
3. **Cut-floor guarantee.** Any group marked `required` must land entirely below the
   *smallest* profile cut in the build (`tiny` = 32,496), or the build fails. This is what
   makes "present in every profile" a checked property.
4. **Reserve ≥ declared surfaces.** A group with 20 `surfaces` and `reserve: 16` is an
   error.
5. **Determinism.** Same spec + same corpus ⇒ byte-identical layout. Ties broken by
   `(-frequency, surface)`, never by dict/set iteration order.
6. **Displacement is reported.** Because Tier 1/2 are full, the build must print and record
   what the new reservations pushed out: *"192 Tier-1 slots to `html` displaced 192 words to
   Tier 2."* Silent displacement is how a build regresses on a use case nobody was watching.

---

## 5. What the build must RECORD (provable, not asserted)

`dict_stats.json` gains a `slots` block:

```json
"slots": [
  {"group": "structural", "reserve": 64,  "placed": 16, "rank_range": [53, 69],
   "tier": 1, "in_profiles": ["tiny","compact","standard","full","reference"],
   "required": true, "source": "explicit"},
  {"group": "html", "reserve": 192, "placed": 192, "rank_range": [69, 261], "tier": 1,
   "source": "data/web_terms_frequencies.txt"}
],
"displacement": [{"from_group": "words", "count": 192, "from_tier": 1, "to_tier": 2}]
```

Consequences: two builds' layouts can be **diffed**; a regression is attributable; and the
`force_include` claim becomes a range assertion rather than a promise.

---

## 6. Cost model (why placement is an economic decision)

| where a surface lands | stream cost per occurrence |
|---|---:|
| Tier 0 (1-char) | 1 |
| Tier 1 (2-char) | 2 |
| Tier 2 (3-char) | 3 |
| Tier 3 (4-char) | 4 |
| **not in the cut → OOV** | **7** (`OOV:A:x`) |

So moving one surface with *n* occurrences from OOV to Tier 1 saves `5n` characters. The
build should be able to **estimate** the saving for a declared group against a named eval
corpus, so `reserve: 192` is a measured decision rather than a guess. That estimate belongs
next to the existing `bench_dict_efficiency` harness.

---

## 7. Migration

- `slots` absent ⇒ current behaviour exactly (`tier1_word_reserve` + frequency). Every
  existing spec builds byte-identically. **Non-negotiable**: v01/v01a must remain
  reproducible.
- `tier1_word_reserve: N` is sugar for `slots: [{group: words, reserve: N}]`. Keep it
  accepted; warn if both are given.
- `force_include: [...]` (implemented today) is sugar for
  `{group: structural, reserve: len(list), surfaces: [...], required: true}`.

---

## 8. Open questions — decide before building

1. **Phrases and Tier 4.** Phrase ids use the `-` prefix (separate id space). Do phrase
   slots participate in this mechanic, or is Tier 4 allocated independently? Current
   builder mixes phrases into the same competing pool, which means a phrase reservation
   would displace words. Needs a decision.
2. **Byte-fallback boundary.** 256 byte-fallback slots sit immediately after `content_size`
   in every profile. If a required group somehow landed above the cut it would collide with
   that region conceptually — confirm the builder cannot produce that state.
3. **Group precedence vs corpus frequency.** If `<div>` is both in the `html` group and
   naturally top-500 by frequency, does it consume an `html` slot or a word slot? Proposal:
   groups are removed from the general pool first (no double-counting), recorded in stats.
4. **Per-profile reservations.** Should a group be able to say "Tier 1 in `full`, Tier 2 in
   `tiny`"? Simpler answer: no — one ordering, one layout. Worth confirming that's
   acceptable for the small-model cuts.
5. **Tier 0's 11 free slots.** `<` `>` `/` `=` `"` are the highest-frequency HTML characters;
   1-char ids would halve their cost again. Tier 0 is currently hand-assigned primitives.
   Is it in scope for slot allocation, or frozen by the LLM vocabulary contract?
6. **Does the LLM vocab contract constrain re-ranking?** `docs/v1/` is a frozen *vocabulary*
   contract. Changing ranks changes ids. Confirm that a new build tag (v01b) is free to
   re-rank, and only a *locked* build is not.

---

## 8b. MEASURED EVIDENCE (2026-07-30) — and two corrections to §2/§8

Three corpora measured: a real CNN page (7,311,041 chars), the 25 published papers
(204,069 chars), and the transcript corpus the dictionary was built from.

### Corrections to this spec's own earlier claims

1. **§8 Q5 was wrong: Tier 0 has ZERO free slots.** `58 PRIMITIVES + 6 RESERVED = 64/64`.
   I derived "11 free" from `tier0_count: 53` in dict_stats; the missing 5 are format
   markers (`STREAM_START`, `STREAM_END`, `CHUNK_BOUNDARY`, `ATTR_DELIMITER`,
   `CONTINUATION`) which are not surfaces and never enter the dictionary.
2. **The reserve is 6, not 12/24.** The design brief reserved 14 (`a`–`n`); the shipped
   config kept only `a`–`f` (the six process-stage markers) and spent `g`–`n` on
   ` `, `\n`, `\t`, `.`, `,`, `:`, `;`, `!`. Of those eight, five earn it outright, three
   are marginal, and `\t` scores **zero** in every corpus measured.

### What the three corpora say

| char | id today | HTML | markdown | transcript |
|---|---|---:|---:|---:|
| `_` | **none** | **111,521** | 527 | — |
| `{` | none (tier 3) | 51,522 | 42 | 4 |
| `}` | none (tier 3) | 50,796 | 42 | 4 |
| `\|` | **none** | 24,874 | 826 | — |
| `>` | none | 15,375 | 66 | — |
| `<` | none | 10,898 | 19 | — |
| `` ` `` | **none** | 6,897 | **2,688** | — |
| `~` | none | 5,483 | 23 | — |
| `\` | `'w'` **unreachable** | 10,092 | 30 | 1 |
| `\t` | `'i'` unreachable | 244 | 0 | 0 |
| `@` | `'6'` | 2,086 | 3 | 247 |
| `%` | `'3'` | 4,705 | 89 | 40,830 |
| `$` | `'4'` | 6,759 | 3 | 22,056 |
| `*` | `'7'` | 4,556 | 2,732 | 713 |
| `=` | `'9'` | **94,612** | 225 | 180 |
| `&` | `'z'` | **75,047** | 9 | 2,039 |

**The cost, quantified.** 287,458 occurrences on that page have no reachable id. At
`OOV:A:x` (7 chars) versus 1 char at Tier 0, that is **~1.72M wasted characters — 23.6% of
the source document.** Web compression isn't underperforming; it's paying a vocabulary hole.

**Two near-misses the measurement caught.** `=` (94,612 in HTML) and `&` (75,047 — HTML
entities) hold Tier-0 ids on transcript frequencies of 180 and 2,039. Judged on transcript
data alone they look like waste; they are among the most valuable web slots. **Do not
demote on single-corpus evidence.**

### The economic insight that avoids the eviction fight

Tier 0 saves 6 chars/occurrence vs OOV; **Tier 1 saves 5.** The marginal value of Tier 0
over Tier 1 is **one character**. So the large win does not require evicting anything:

| action | occurrences | chars saved | displacement |
|---|---:|---:|---|
| rank-floor `\` + `\t` (already hold Tier-0 ids) | 10,336 | ~62k | **none** |
| the 8 unmapped chars OOV → **Tier 1** | 277,366 | ~1.39M | 8 of 1,280 word slots |
| additionally `_` Tier 1 → **Tier 0** | 111,521 | +112k | 1 Tier-0 eviction |

**~1.45M of the ~1.72M available (84%) is captured with no Tier-0 eviction at all.** Only
`_` (111,521) has the volume to justify contending for a 1-char id.

### Proposed v01b layout (target: web + document)

1. **Rank-floor every Tier-0 primitive into every cut.** Free, no displacement, fixes `\`
   and `\t` and the eight Tier-0 chars currently missing from `tiny` (`* + @ = #` …).
   *This is priority one and is a bug fix, not an allocation choice.*
2. **Tier 1 structural group** — `_ { } | > < `` ` `` ~` (8 slots of 1,280).
3. **Tier 0: consider `_` only.** 111,521 occurrences vs the weakest incumbent `\t` (0).
   But `\t` is the *word-processor* target's most important character — so this is a
   **target decision**, and the honest resolution is two dictionaries, not one compromise.
4. **Evict nothing else.** `%`, `$` earn their slots on transcripts; `*` on markdown;
   `=`, `&` on HTML.

### What this settles

The same character demands different placement per target — `` ` `` is #1 for documents and
mid-pack for web; `_` is #1 for web and rare in documents; `\t` is worthless in both and
critical for a word processor. **Frequency ranking over one corpus cannot express that.**
That is the case for §3's declarative slots, now measured rather than argued.

---

## 9. Why this is worth doing before the next build

The audit found one fatal bug and a systemic cost, both from allocation being implicit. The
mechanic above turns allocation into a declaration that is validated, recorded, diffable,
and costed. It also generalizes: the same grammar serves a legal-domain dictionary, a
code-domain dictionary, or a small on-device cut — each stating its own priorities, and each
provable against them.

Question 5 is the one most likely to change the answer: if Tier 0's free slots are
available, the cheapest HTML characters get 1-char ids and the web-capture regression may
disappear outright.
