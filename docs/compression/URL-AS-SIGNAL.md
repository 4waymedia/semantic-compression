# URLs Are Not Noise — Side Note on URL Handling in the Dictionary and the Browser

> Status: **note, not a spec.** Written 2026-07-30 from the long-entry review of
> `elo-browser-v01b`. Nothing here is built. Recorded so the next person does not
> re-derive it, and so URLs are not culled as junk in a dictionary cleanup.

---

## Where this came from

Reviewing the 100 longest single-word dictionary entries for a cull, the largest
category was not garbage — it was **URLs**:

```
members.americancontingency.com          freq 2
integrativemedicine.arizona.edu          freq 1
christianwarriortraining.com             freq 6   (live in the full cut)
elizabethsmartfoundation.org             freq 1
globalcompassioncoalition.org            freq 2
```

These arrived from transcripts — podcast hosts reading sponsor and resource URLs
aloud — and from the web-structure corpus. The instinct on a cleanup pass is to
delete them: they are long, rare, and look like noise next to real words.

That instinct is wrong, and the reason is worth writing down.

## A URL is a compressed assertion about a topic

`integrativemedicine.arizona.edu` is not a random string. It carries, in 31
characters and without a single inference step:

| signal | value here |
|---|---|
| topic | integrative medicine |
| institution | University of Arizona |
| domain class | `.edu` — academic |
| implied reputation | institutional, non-commercial |
| implied context | the speaker is citing a source, not selling |

Compare `primalbeef.com`: same shape, `.com`, commercial, product. A speaker
saying one is doing something categorically different from a speaker saying the
other, and the URL says so before any language model looks at the sentence.

This is the same argument the project already makes about fillers — a signal that
looks like noise is carrying involuntary information about speaker state. A URL
is the same class of thing, carrying information about *source* rather than state.

## Three distinct use cases

**1. Topic anchoring.** A URL's domain and path segments are topic tokens that
someone else already curated. `arizona.edu/integrativemedicine` names its subject
more reliably than the surrounding sentence does, because a human chose the path.
This is a cheap, deterministic topic signal that needs no inference and no model.

**2. Source reputation.** TLD and domain give a coarse prior — `.edu` / `.gov` /
`.org` / `.com` — which the constraint cascade could use as evidence weight when
two claims conflict. Note the boundary: this is a *prior*, not a truth judgment,
and it belongs in the evidence layer, never as a hard filter. A `.com` is not
wrong for being a `.com`.

**3. Cross-transcript linking.** The same URL appearing in two conversations is a
hard join key — far stronger than topic similarity, because it is an identity
match rather than a distance measure. This is System 4 territory (cross-transcript
semantic linking) and should not be built in System 1.

## URLs do not belong in the word dictionary

The word dictionary is the wrong container. A URL is not a word that happens to be
long — it is a **structured record with four parts that behave differently**, and
flattening it into one surface destroys all four. The proposal is a separate URL
store, decomposing:

```
https://ign.com/movies/latest?name=spiderman&v=ssdlafk;jfdsa
        └──────┘└─────┘└────┘ └────────────┘ └────────────┘
         base    section       usable param    opaque param
```

| part | role | storage |
|---|---|---|
| **base** (`ign.com`) | source identity, reputation prior | host table, small id |
| **section** (`/movies`) | topic | split into segments; most are already words |
| **usable param** (`name=spiderman`) | topic refinement | key table + value via the word dictionary |
| **opaque param** (`v=ssdlafk…`) | tracking, session, cache-bust | verbatim, no semantics, no id |

Measured across two captured pages, each part behaves as the split predicts:

| | cnn | tomshardware |
|---|---:|---:|
| URLs | 2,845 | 2,739 |
| distinct hosts | 137 (**21x reuse**) | 87 (**31x reuse**) |
| path segments | 9,652 → 535 distinct (**18x**) | 4,645 → 1,262 distinct |
| segments already in the word dictionary | **76%** | 39% |
| param values that are words | 183 | 29 |
| param values opaque | **2,344 (93%)** | 58 (67%) |

Three consequences fall out of those numbers:

1. **Hosts repeat 21–31x within a single page** and vastly more across a corpus. A
   host table with short ids is a large compression win that the word dictionary
   cannot capture, because it stores `www.cnn.com` as one rare 11-character surface
   rather than one id used 700 times.
2. **Path segments need splitting, not a new vocabulary.** 76% of cnn's segments
   are already dictionary words. `/movies` is `movies`. The work is decomposition,
   not vocabulary growth.
3. **The param split is the opaque-data rule again.** `name=spiderman` has a known
   word on the right; `v=ssdlafk;jfdsa` does not. The same test that separates
   language from blobs at the token level separates useful params from tracking —
   one rule, two places. On cnn, 93% of param values fail it.

**Do not cull URLs in a long-entry cleanup** while this is unbuilt — they are the
one long-entry category with genuine semantic content, and deleting them now loses
the corpus evidence for building the store later. Cull the ASR repeat artifacts
(`modernwisdom.dom.dom.dom.dom`) instead; those are transcription noise with no
referent.

**Losslessness is the hard constraint.** A decomposed URL must reassemble
byte-exactly — scheme, host case, port, every path separator including trailing
slashes, original parameter order, percent-encoding, and fragment. Reassembly that
is merely *equivalent* is not good enough. Any URL that cannot be decomposed and
recomposed byte-exactly must fall back to verbatim storage; the store is an
optimisation, never an authority on what the URL was.

**But do not treat them as words either.** A URL should not receive a word's EPA
vector by lemma matching, and `christianwarriortraining.com` should not compose an
affect score from `christian` + `warrior` + `training`. That is a composition the
EPA layer would happily perform and it would be meaningless.

The likely right shape, unbuilt:

- a facet flag distinguishing `URL` from `WORD`, alongside the existing bucket/utility
  fields — the facets layer already has the record space for it
- URL entries excluded from EPA composition, included in topic extraction
- the **opaque-data threshold** (longest reachable single-word entry, currently 29
  chars) computed from *lexical* entries only, so a long domain does not widen the
  gate that separates language from blobs

## What this implies for the ELO Browser

The browser is where URLs arrive in volume and with the most context — it has the
page, the link text, and the navigation history that a transcript does not.

- **Structural extraction beats tokenization.** `href` values, canonical links and
  `og:url` are exact; recovering a URL by tokenizing page text is guesswork. The
  browser should lift them from the DOM.
- **Split, don't store whole.** `integrativemedicine.arizona.edu` as one dictionary
  entry is a 31-character token used once. As `integrative` + `medicine` +
  `arizona` + `edu` it is four known words plus a structure marker — better
  compression *and* better semantics. Note this conflicts with the long-entry
  review's compression view, where a single rare id is cheaper; the split is worth
  it only because the parts are semantically live.
- **Link text is a gloss.** `<a href="...">integrative medicine at Arizona</a>` is
  a human-written description of the target. That is a free, curated label for the
  URL — the same role a `RAISES_QUESTION` edge plays between seeds.
- **Visited URLs are episodic memory.** A URL the user actually opened is a
  stronger signal of interest than one merely present on a page. That belongs in
  the user store, under the existing memory/storage axes, not in the dictionary.

## What NOT to do

- **Do not fetch URLs to enrich them.** Deterministic, no-inference, no-network is
  the System 1 contract. Reputation from a TLD is a lookup; reputation from
  crawling is a different system with different failure modes.
- **Do not let a URL's topic override the sentence's.** It is one evidence source
  among several, and the person may be citing it to disagree with it.
- **Do not build any of this in System 1.** Topic anchoring touches System 2,
  reputation priors touch System 3, cross-transcript joins are System 4. The only
  System 1 obligations are: keep URLs in the dictionary, mark them as URLs, and
  keep them out of EPA composition.

## Open questions

- **Where does the URL store live?** A sub-DB alongside `forward`/`reverse`/`facets`
  keeps it in the same LMDB and the same fingerprint; a separate artifact keeps the
  word dictionary's identity clean but adds a binding to enforce. The facets layer
  already sets the precedent for a sub-DB.
- **Do host ids belong in the token stream or beside it?** If a URL becomes
  `HOST(id) SEG SEG PARAM…` inline, the codec stays single-pass. If it becomes a
  reference into a side table, the stream shortens but decode needs both artifacts
  present — the same family-binding problem `DICTIONARY-FAMILY-INTEGRATION` solves
  for epa/facets/neighbours.
- **Should the TLD be a primitive?** There are few of them and they recur
  constantly, and the TLD is exactly where the reputation prior lives.
- **What counts as a "usable" param key?** `name=`, `q=`, `search=`, `id=` differ in
  value. The lexical test handles the *value*; the key may need its own list.
- **Caveat on the measurements above.** URLs were extracted with a regex over raw
  HTML, and the param keys came back as `amp;q`, `amp;gpp_sid` — the `&amp;` entities
  were never decoded. So the param counts are right in magnitude but the keys are
  mangled, and a real implementation must decode entities before parsing. The
  browser lifting `href` from the DOM would not have this problem, which is itself
  an argument for structural extraction over text scraping.
