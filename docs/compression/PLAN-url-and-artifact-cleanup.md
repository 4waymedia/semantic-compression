# Plan — URL Extraction, Artifact Cleanup, and a URL Span Marker

> Status: **plan, not yet implemented.** Written 2026-07-30 against
> `elo-browser-v01b` (437,995 entries, fingerprint `495cd535…`). Targets the next
> dictionary build. Companion to `URL-AS-SIGNAL.md` (why) and
> `LONG-ENTRY-REVIEW.md` (what was found).

---

## Scope, measured

| category | entries | live in `full` | corpus occurrences | freq == 1 |
|---|---:|---:|---:|---:|
| URL-shaped | **3,490** | 486 | 16,483 | 1,975 |
| ASR artifacts | **19** | 3 | 73 | 11 |

Two corrections to earlier assumptions, both from measuring rather than eyeballing:

**ASR artifacts are not a category.** Nineteen entries. The 88-character
`switcheroooo…` made the problem look systemic; it is a curiosity. Of the three
live ones, `hahaha` (21 occurrences) is a legitimate conversational token and
should be *kept* — only `redlinining` and `trainining` are stutter defects.
Filtering these is worth doing because it is nearly free, not because it matters.

**The URLs are mostly not URLs.** The highest-frequency entries are
`underground.com` (650), `originusa.com` (300), `beef.com` (275), `nt.com` (250) —
fragments of *spoken* sponsor reads, truncated or mis-segmented by ASR. Nobody can
visit `nt.com`. They are transcription debris that happens to be shaped like a
domain, and they consume 486 live slots.

> **Detector warning, learned the hard way.** A first pass matched any surface
> containing `/` as a URL and flagged `</a>` (16,486), `</p>`, `</li>`, `</ul>` —
> the most common HTML closing tags in the corpus. A second matched any repeated
> chunk as an ASR artifact and flagged `000` (27,148) and `2000`. **No cut may run
> on a detector whose output has not been eyeballed at the top and the tail.**
> The detectors below are the corrected ones and still deserve review before use.

```python
TLD = ('com','org','net','edu','gov','io','co','uk','us','tv','me','info',...)

def is_url(s):
    if any(c in s for c in '<>"\'{}()[]'): return False   # markup, not a URL
    if s.startswith('/') or s.startswith('.'): return False
    if '/' in s and re.match(r'^[a-z0-9][a-z0-9._-]*\.[a-z]{2,}/', s): return True
    parts = s.split('.')
    return (len(parts) >= 2 and parts[-1] in TLD
            and all(p and re.fullmatch(r'[a-z0-9-]+', p) for p in parts))

def is_asr(s):
    if not re.fullmatch(r'[a-z.]+', s): return False       # letters only, no digits
    return bool(re.search(r'([a-z]{2,6}\.?)\1{2,}', s))    # alphabetic chunk x3+
```

---

## Change 1 — stop admitting URLs as word entries

**Where:** the miner / `build_from_spec` corpus resolution, not a post-hoc delete.
A surface removed from the dictionary but still present in `word_frequencies.txt`
returns on the next build.

**What happens to the tokens:** they do not disappear. A URL still tokenizes and
still encodes losslessly — it becomes a bracketed span (Change 3) whose interior is
ordinary tokens. `jockounderground.com` stops being one rare 20-character entry and
becomes `jocko` + `underground` + `.` + `com`, three of which the dictionary already
knows.

**Expected effect:** 486 live slots freed; 3,490 entries removed; the opaque-data
threshold unaffected (it is set by a CSS class, see Change 4).

### MEASURED (2026-07-30) — and it changes what this work is

The tokenizer **already splits URLs**. `https://ign.com/movies/latest?name=x` →
`['https', ':', '/', '/', 'ign.com', '/', 'movies', '/', 'latest', '?', 'name',
'=', 'x']`. Path segments, query keys and values are already separate tokens
hitting the word dictionary. Only the **host** stays whole. So this was never a
question about URLs — it is a question about hosts, and the plan's framing was
wider than the problem.

Four options, encoded against the v01b `full` cut, `len(id) + 1` per token:

| option | cnn | tomshardware | google-search |
|---|---:|---:|---:|
| remove hosts from the dictionary (OOV) | **+0.00%** | **+0.00%** | +0.01% |
| split hosts into labels (`ign` `.` `com`) | −0.05% | **+0.87% worse** | +0.10% |
| back-reference repeated hosts | −0.56% | −2.25% | −1.31% |
| **back-reference + URL brackets** | **−0.34%** | **−1.70%** | **−0.92%** |

**Change 1 is free.** Removing 3,490 URL entries costs +0.00%. Most hosts occur
once, so a rare Tier-3 id and an OOV record cost nearly the same. 486 live slots
and 3,490 entries reclaimed for nothing measurable — do it.

**Splitting hosts is `REFUTED`.** It is *worse* on tomshardware. Host labels do not
recur enough to pay for the extra tokens and separators. Drop that idea; it was
plausible and wrong.

**Back-referencing is the only real gain**, and it tracks host reuse — best where
reuse is highest (tomshardware, 31x). Brackets consume roughly 40% of it, and the
combination is still net positive on all three pages.

> ### What this means for the whole plan
>
> The best case is **1.7%**. This is not a compression feature.
>
> A parser, a conservative identifier, a back-reference table, five losslessness
> tests and two Tier-1 evictions is a large amount of machinery to buy ~1% of
> stream size. If compression were the justification, the honest answer would be to
> not build it.
>
> It is justified by **semantics** — topic anchoring, source reputation, and
> cross-transcript identity joins — and the useful finding is that the semantic
> markup **pays for itself** rather than costing. That is the argument to make and
> the one to defend: we are not compressing better, we are adding meaning for free.

---

## Change 2 — filter ASR repeat artifacts

**Where:** same place, the miner.

**What:** drop surfaces matching `is_asr` **with a frequency floor** — a repeated
chunk that occurs thousands of times is a real word or a real filler, not a defect.
Suggested rule: `is_asr(s) and freq < 50`. That keeps `hahaha`, drops
`modernwisdom.dom.dom.dom.dom`.

**Expected effect:** ~16 entries. Negligible for compression; worth doing because
these entries are the ones that make the *longest-entry* statistic meaningless, and
that statistic now governs the opaque-data gate.

---

## Change 3 — a URL span marker (the design question)

The requirement: mark a span as a URL so the semantic layer can treat it as a URL,
without breaking byte-exact round-trip.

### Recommended: a bracket pair

Two new primitives, `URL_START` and `URL_END`. The interior is **encoded exactly as
it is today** — ordinary tokens, including the `.` `/` `:` separators, which are
already dictionary entries.

```
https://ign.com/movies  ->  URL_START https :// ign . com / movies URL_END
```

Why this shape:

- **Lossless by construction.** The interior encoding does not change, so it cannot
  introduce a round-trip failure. The brackets add information; they remove none.
- **The semantic layer gets what it needs immediately.** Everything between the
  brackets is a URL: skip EPA composition, run topic extraction on the segments,
  route the host to the reputation prior.
- **It leaves the compression door open.** Any future structured encoding — host
  table, implied separators, the four-part split in `URL-AS-SIGNAL.md` — happens
  *inside* the brackets and is invisible to the semantic layer. The bracket contract
  does not change when the interior gets smarter. That decoupling is the main reason
  to prefer this over a structured encoding now.

**Cost:** 2 ids + 2 delimiters per URL. At Tier 1 that is 6 characters. For a
30-character URL it is acceptable; for a page with 2,845 URLs (cnn) it is ~17,000
characters, against 486 freed slots and better segment reuse. **Net effect
unmeasured — measure before shipping.**

### Rejected: terminator only

`URL_END` alone, with the decoder scanning backwards to find the start. Ambiguous
(where does the URL begin in `see ign.com/movies now`?) and fragile. A span needs
two edges or an explicit length.

### Alternative: `URL_START` + token count

`URL_START <n> tok tok … tok`. Saves one delimiter versus a bracket pair and gives
the decoder the span length up front. Costs a variable-length integer in the stream
and complicates the tokenizer's streaming contract. Worth considering only if the
bracket pair measures badly.

### The slot question — **DECIDED: Tier 1**

Every tier is full by construction — the builder fills each to capacity by rank — so
any new id evicts something. The question is only which tier's marginal entry is
cheapest to displace, and that is not the tier with the cheapest evictions:

| markers in | bracket cost (16,391 URL occurrences) | eviction cost | total |
|---|---:|---:|---:|
| Tier 0 (1-char) | 16,391 × 4 = **65,564** | 0 — spends 2 System-2 slots | **65,564** |
| **Tier 1 (2-char)** ← chosen | 16,391 × 6 = **98,346** | 31,935 | **130,281** |
| Tier 3 (4-char) | 16,391 × 10 = **163,910** | ~2 | 163,912 |

Tier 3 has almost free evictions (marginal entries occur once) and is still the
worst option, because the per-URL bracket cost dominates the one-off eviction cost
by four orders of magnitude. Tier 0 is cheapest but spends 2 of the 6 slots
reserved for System 2, which `CLAUDE.md` forbids System 1 from doing. **Tier 1 is
the best available choice that respects the reserve**, and the 64,717-character
premium over Tier 0 is 0.02% of the corpus.

> ### ⚠ The eviction must be chosen, not computed
>
> Tier 1 is at 1,280 of 1,280 (20 first-chars × 64 — a structural limit, not a
> tuning parameter). Two markers means two evictions, and **the lowest-frequency
> Tier-1 entry is `|` at frequency 0** — the pipe that `force_include` deliberately
> placed there this morning to keep the stream frame intact.
>
> An automatic "evict the lowest-frequency Tier-1 entry" rule would silently undo
> that fix and reintroduce the markdown frame corruption. Forced surfaces are
> exempt from eviction by definition; the rank floor that puts them there must also
> protect them from being displaced.
>
> The two actual eviction candidates are `<abbr ` (15,967) and `</abbr>` (15,968),
> which drop to Tier 2 and cost 1 character each per occurrence.

---

## Change 4 — derive the opaque-data threshold from lexical entries only

Not strictly part of this cleanup, but it interacts. The threshold that separates
language from blobs is the longest reachable single-word entry: **29 characters**,
currently set by `focus-visible:justify-between` — a Tailwind class. The longest
reachable *purely alphabetic* entry is `congressionallymandated` at 23.

Computing the threshold over lexical entries only tightens the gate 29 → 23 without
cutting the 1,844 CSS classes that legitimately earn their ids on web content
(16,229,956 corpus occurrences, 33 of them in Tier 1).

This requires a lexical/non-lexical mark on entries, which the facets layer has room
for.

---

## Change 5 — identify / parse / save, as three separate things

The capability is "identify, parse, and save a URL losslessly". Those are three
jobs with different failure modes, and conflating them is how a URL handler becomes
a security bug.

### Parse — solved, and lossless on *any* input

```python
URL_RE = re.compile(r"""
    (?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://|//)?   # 'https://', '//', or absent
    (?P<host>[^/?#\s]*)                          # host[:port], verbatim
    (?P<path>/[^?#\s]*)?                         # leading '/' KEPT
    (?P<query>\?[^#\s]*)?                        # leading '?' KEPT
    (?P<frag>\#\S*)?                             # leading '#' KEPT
    $""", re.X)

def parse(u):
    m = URL_RE.match(u)
    if not m: return None
    d = {k: (v or '') for k, v in m.groupdict().items()}
    d['host_parts'] = d['host'].split('.') if d['host'] else []
    d['path_parts'] = d['path'].split('/') if d['path'] else []
    return d

def unparse(d):
    return (d['scheme'] + '.'.join(d['host_parts'])
                        + '/'.join(d['path_parts'])
                        + d['query'] + d['frag'])
```

**The principle that makes it lossless: every separator lives inside a part.** The
scheme carries its `://`, the path carries its leading `/`, the query its `?`, the
fragment its `#`. Splitting on `.` and rejoining on `.` is exactly reversible.
Reassembly is concatenation, never reconstruction.

**Nothing is normalised.** No lowercasing the host, no percent-decoding, no
stripping default ports, no collapsing `//`, no adding or removing trailing
slashes. Every one of those is a correctness bug here, however tempting: `IGN.COM`
and `ign.com` address the same server and are different bytes, and bytes are what
we owe the caller.

**Measured:**

| corpus | cases | exact round-trip |
|---|---:|---:|
| distinct URL-shaped strings from the three captured pages | 16,743 | **100.00%** |
| hand-written edge cases | 39 | **100.00%** |

Edge cases covered: trailing slash, mixed case, `//` protocol-relative, `:8080`
ports, `user:pw@host`, `a..b.com` empty labels, `ign.com//`, `?a=&b`, `?flag`,
`?a=1&a=2`, bare `?`, bare `#`, `%20` and `%2F`, IDN (`münchen.de` and its
punycode), `[::1]:80`, `localhost:3000`, `?onlyquery`, `#onlyfrag`, `/onlypath`,
and the empty string.

### Identify — a separate, conservative decision

The parser round-trips `javascript:alert(1)` and `mailto:a@b.com` perfectly,
parsing them as a "host". That is the parser behaving correctly — it is lossless on
arbitrary input — and it is precisely why **identification must not be the parser's
job**. A browser that treats `javascript:` as a URL has a script-execution problem,
not a compression problem.

Identification is therefore:

- an **allow-list of schemes** (`http`, `https`, and bare host-shaped strings), not
  a deny-list
- a **real TLD check** for scheme-less candidates, so `nt.com` still qualifies but
  `version.2` does not
- **conservative by default**: an unidentified candidate is encoded as ordinary
  tokens. Nothing breaks, we merely forgo the semantics.

Because parse is lossless on everything, a false negative in identify costs
compression and semantics but never correctness. A false *positive* costs
semantics — and, in the browser, potentially safety. Bias the detector toward
missing URLs.

### Save — bracket, parts, verbatim fallback

```
URL_START  <scheme>  <host parts>  <path parts>  <query>  <frag>  URL_END
```

with three rules:

1. **Host**: stream-local back-reference if this host appeared earlier in the
   document, otherwise verbatim. No shared table, so a stream decodes with nothing
   but itself. (Captured pages reuse each host 21–31x, so this recovers nearly all
   of the available gain.)
2. **Path segments**: ordinary word-dictionary ids — 39–76% of segments are already
   dictionary words.
3. **Anything else** — query strings, fragments, opaque params — **verbatim**. They
   are 67–93% opaque and there is nothing to gain by pretending otherwise.

And the invariant that governs all of it: **if `unparse(parse(u)) != u`, emit the
whole URL as verbatim tokens.** The parser has not failed on 16,782 tested inputs,
but the fallback is what makes losslessness a property of the design rather than a
property of the test suite.

## Change 6 — a curated domain set with AUTHORED semantics

Change 1 removes 3,490 mined URL entries. This change puts a much smaller, better
set back — not for compression (measured at +0.00% either way) but because **a
dictionary id is the join key into every semantic channel**, and a stable id for
`cnn.com` is useful across products that have nothing to do with compression.

### What a dictionary entry actually buys, verified against `elo-browser-v01b`

| channel | for the 482 live URL entries today | verdict |
|---|---|---|
| vocab index | present — the join key | ✅ the whole point |
| **facets** | present, and **authorable** via `facet_overrides.tsv` (111 in use) | ✅ **the payload** |
| neighbours | present — 16 each, same as `happy`/`murder` | ⚠️ quality unverified |
| **EPA** | **NaN** for all 482 | ✅ **correct — leave it** |

### EPA stays NaN, deliberately

`Epa::get` returns `None` on NaN (`epa.rs:81`), aggregation counts only rated
words, and coverage is reported honestly. 53,316 of 261,872 live entries (20.4%)
are NaN — it is the established "unrated" sentinel, and it is a better one than
`0,0,0`, which would conflate *neutral* with *unknown*.

**Do not fill it for domains.** Three reasons, in increasing order of seriousness:

1. **It is a different kind of EPA.** Everything in `epa.bin` traces to Warriner
   and NRC-VAD — measured lexical norms from surveys of real people. Affect Control
   Theory does assign EPA to institutions, so the idea is theoretically sound, but
   institutional EPA is not lexical EPA and does not belong in the same array.
2. **Generated values would be indistinguishable from measured ones.** 500 domains
   is 1,500 float32s sitting beside `happy = (3.47, 2.21, 1.05)`, carrying the same
   fingerprint, with nothing marking them as guesses. That is the precise failure
   `CLAUDE.md`'s hypothesis-not-fact loop exists to prevent, and it violates the
   standing no-LLM-in-the-learning-loop rule.
3. **For news domains it is an editorial act, not a technical one.** The affect of
   `cnn.com` or `foxnews.com` is contested, audience-dependent and time-varying.
   Encoding a valence for news organisations into a deterministic substrate puts a
   political thumb on the scale of every downstream affect computation, in a system
   that presents itself as measurement. This one should not be in the dictionary at
   any confidence level.

If institutional EPA is wanted later, source it from **measured** ACT dictionaries,
in a separate channel, marked as such.

### Neighbours from co-occurrence, not from a model

Domains that appear in the same transcripts and pages genuinely relate. That is
deterministic, reproducible from committed artifacts, and cheap. Asking a model
which sites are similar is not.

The 16 neighbours each domain currently carries come from embedding the literal
string `"shopify.com"` with all-mpnet. **Whether those are meaningful or noise is
unverified** — decode a sample before relying on them. If they are noise, the
authored facets carry the whole load, which is an acceptable outcome.

### Selection

- **Replace, do not add to.** The 3,490 mined entries are ASR debris — `nt.com`,
  `beef.com`, `underground.com`, fragments of spoken sponsor reads. Net effect of
  cut-plus-curate is roughly −3,000 entries with strictly better semantics.
- **The list is a union, not a ranking.** Top-N-worldwide gives forward coverage for
  the browser and future products; domains actually observed in our corpora give
  immediate value. Neither alone is right — half a global top-500 will never appear
  in a transcript.
- **Every curated domain ships with an authored facet override.** The entry without
  the override is just a cheaper id; the override is the reason to do this.

```
exact:cnn.com        SOURCE   NEWS      # curated domain, authored not derived
exact:apple.com      SOURCE   COMMERCE
exact:arxiv.org      SOURCE   ACADEMIC
```

### Fetched metadata goes to the mutable store, never here

The browser's fetch-and-read-meta feature (see `URL-AS-SIGNAL.md`) populates the
**mutable host knowledge base** with provenance — `ign.com claims games/media`.
It must not write into `facets.bin` or `epa.bin`.

**The line:** the dictionary's binary channels are frozen, fingerprinted, and
traceable to measurement or to a human decision recorded in the repo. Anything
generated or fetched lives in the mutable store beside them, carrying provenance
and revisable. The moment generated values enter `epa.bin`, the fingerprint stops
meaning what it currently means — and the family-binding guard would validate it
happily, because it checks *consistency*, not *provenance*.

## Losslessness requirements (non-negotiable)

Every one of these must hold, and each needs a test before the build is accepted:

1. **A URL round-trips byte-exactly** — scheme, host case, port, every separator
   including trailing slashes, percent-encoding, parameter order, fragment.
2. **A bare domain in prose round-trips** — `visit ign.com today` must survive,
   including the surrounding spaces.
3. **A malformed or partial URL round-trips** — `nt.com`, `http://`, `://x` must not
   crash the bracketing and must reassemble verbatim.
4. **Anything the bracketer cannot handle falls back to verbatim tokens.** The
   marker is an optimisation, never an authority on what the bytes were.
5. `verify_lossless` passes on all 10 sample formats **and** on the three captured
   pages, which it does not currently cover.

---

## Sequencing

1. Review and sign off the two detectors on their full output — not just the head.
2. Add the miner filters (Changes 1 + 2) behind a build-spec flag, so a build can be
   produced with and without them for comparison.
3. Build both. Compare ratio, OOV%, entry count and threshold on the captured pages
   and the book set.
4. Only if that measures well: implement the bracket pair, with the five losslessness
   tests written **before** the encoder change.
5. Defer the host table and four-part decomposition until the bracket contract is
   stable and shipped.

## Open questions

- Do the ASR-fragment domains (`beef.com`, `nt.com`) have any residual value as
  evidence that a sponsor read occurred? They are debris as URLs but may be signal
  as *ad markers*. Currently assumed no.
- Should `www.` be a primitive? It prefixes a large fraction of hosts and carries
  no information.
- Does bracketing interact with `_longest_match_scan`? A phrase spanning a URL
  boundary would be a defect; the scan must not match across a bracket.
