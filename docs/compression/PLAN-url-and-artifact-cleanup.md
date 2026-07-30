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

**Risk:** splitting increases token count for URL-heavy documents. Each URL costs
more tokens but each token is cheaper and reusable. **This must be measured on the
captured pages before the change is kept** — it is the same trade the long-entry
review flagged, and it could go either way.

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

### The slot question — **needs your decision**

Tier 0 is full: 58 primitives + 6 slots (`a`–`f`) reserved for System 2. So:

- **Tier 1 (2-char ids):** 6 characters of overhead per URL, no policy conflict.
  Recommended.
- **Tier 0 (1-char ids):** 4 characters per URL, but spends 2 of the 6 System-2
  reserved slots. `CLAUDE.md` forbids System 1 consuming that reserve.

The saving is 2 characters per URL. On cnn's 2,845 URLs that is 5,690 characters —
about 0.08% of the page. **My recommendation is Tier 1**: the reserve exists for a
reason, and 0.08% is not a reason to spend it.

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
