# The EloAI Annotation Handbook — Facets, Meta & EPA  · MOVED

> **This handbook has moved to `semantic_compression/handbook/facets-epa/HANDBOOK.md`**
> (the new colocated `handbook/<concept>/` convention — see `handbook/README.md`).
> This file is kept as a redirect; update any links. The deep specs stay here in
> `docs/compression/`.

---

## 1. The three layers at a glance

Every word/phrase in the dictionary gets three kinds of annotation, from coarsest
to richest:

| Layer | What it is | Size | Example for `because` |
|---|---|---|---|
| **Facet** | a coarse machine tag: *what kind, what role, how to treat* | 4 bytes | bucket=RELATION, utility=FUNCTION, cues=CAUSE,EVIDENCE_CUE |
| **Meta** | richer descriptive fields (abstraction, causality, domain…) | a row | causality=CAUSE,EVIDENCE; abstraction=– |
| **EPA** | the *feeling* vector — three numbers | 3 floats | (E,P,A) — `because` is a connector, near-neutral |

The facet is what the compressor reads on every token (fast). The meta is the
queryable detail. The EPA is the emotional/semantic coordinate. They never
contradict — the facet is a compiled summary of the richer layers.

---

## 2. The terms (glossary)

### 2.1 Bucket — *what kind of thing is this?*
| Value | Meaning | Examples |
|---|---|---|
| `TOPIC` | a concrete thing / nameable entity or subject | rabbit, ocean, laptop, NASA |
| `CONCEPT` | an abstraction or domain idea (often abstract or multiword) | freedom, justice, machine learning |
| `METHOD` | an action / process / procedure (usually a verb) | analyze, optimize, measure |
| `RELATION` | a connector or logical operator | because, however, if, not, all |
| `STRUCTURAL` | punctuation / markup / whitespace | `?` `.` `,` |
| `UNKNOWN` | filler or genuinely unclassifiable | um, uh |

*Rule of thumb:* concrete → TOPIC, abstract → CONCEPT, action → METHOD,
glue/logic → RELATION.

### 2.2 Utility — *how should a machine treat it?*
`CONTENT` (carries topic meaning) · `FUNCTION` (grammatical glue) ·
`STRUCTURAL` (punctuation) · `FILLER` (speaker noise). Use it to keep or drop
tokens: a topic index keeps CONTENT, a summarizer drops FILLER.

### 2.3 Logic cues — *what reasoning role does it afford?* (a set; may be empty)
A word can carry several at once (`when` = CONDITION + QUESTION + TEMPORAL).
| Cue | Signals | Cue | Signals |
|---|---|---|---|
| CAUSE | cause→effect (because) | NEGATION | negates (not) |
| CONDITION | if/when | QUANTIFIER | all/some |
| INFERENCE | therefore/so | MODAL | must/should |
| EVIDENCE_CUE | for example | QUESTION | why/what |
| CONTRAST | but/however | TEMPORAL | before/after |
| CONCESSION | although | COMPARISON | than/like |
| DEFINITION_CUE | is defined as | CONJUNCTION | and/or |
| CLAIM_CUE | asserts a claim | | |

### 2.4 Flags — *facts about the label itself*
`MULTIWORD` (a phrase) · `CLOSED_CLASS` (function word) · `MANUAL` (a human set it,
via override) · `HEURISTIC` (a rule guessed it) · `AMBIGUOUS` (genuinely unreliable).
MANUAL beats HEURISTIC — overrides always win.

### 2.5 Meta axes (System-1 deterministic)
- **abstraction** — `concrete` | `abstract` | `metaphor`. (love=abstract, rabbit=concrete)
- **causality** — which of CAUSE/CONDITION/INFERENCE/EVIDENCE the word affords.
- **temporality** — `temporal` if it locates in time (before, when).
- **scope** — `quantified` (all, every, some).
- **domain** — coding/medical/finance/… (filled from corpus provenance; often blank).
- **register** — formal/technical/colloquial (from the source it came from).

### 2.6 EPA — the feeling vector (Evaluation, Potency, Activity)
Three numbers capturing affect (Osgood's universal axes):
- **E — Evaluation:** good ↔ bad (positive vs negative).
- **P — Potency:** strong ↔ weak (powerful vs powerless).
- **A — Activity:** active ↔ passive (lively vs calm).

---

## 3. How to read the ratings (the numbers)

**The scale.** Each EPA axis runs from **−4 to +4** (the canonical scale; some
source data arrives as −1…+1 or 1–9 and is converted to ±4). What matters is the
**sign** and the **magnitude**:

```
sign       +  = the positive pole (good / strong / active)
           -  = the negative pole (bad  / weak  / passive)
magnitude  how strongly — 0 is neutral, the bound (±4 or ±1) is the extreme
```

So `+1` on a ±4 scale = mildly positive; `+1` on a ±1 scale = *maximally* positive.
`-0.3` = mildly negative either way. **Always know the scale's bound, then read
proportionally.** Reading bands (on ±4):

```
|v| < 0.5   ~ neutral / negligible
0.5 – 2.0   moderate
> 2.0       strong
```

**Reading a triple — worked examples** (real values, ±4 scale):
```
love      ( +3.0,  +0.9,  +0.4 )   strongly GOOD, a little strong, mildly active
victory   ( +2.6,  +2.0,  +0.9 )   strongly good AND strong (triumph)
powerful  ( +1.5,  +2.0,  +0.4 )   positive, notably STRONG (potency-led)
calm      ( +1.9,  +2.4,  -3.3 )   good + strong but very PASSIVE (low activity)
death     ( -3.1,  -1.6,  +0.5 )   strongly BAD, weak, barely active
disaster  ( -3.3,  -2.0,  +1.4 )   strongly bad, weak, somewhat active (turmoil)
```
Read left-to-right as *(how good, how strong, how active)*. `calm` shows why three
axes matter: it's pleasant and potent yet deeply passive — one number couldn't say that.

**Confidence** (`high`/`med`/`low`) tells you how much to trust a *label*; on EPA,
the substrate also tags per-axis confidence (e.g. a word's Evaluation may be solid
while its Potency is uncertain — see EPA.md). Low confidence = review candidate.

**Notation:** `-` means none/empty; cue fields are comma-separated sets
(`CAUSE,INFERENCE`); a missing EPA = the word isn't rated (neutral, not "0 is true").

---

## 4. Adjusting for the result you want (cookbook)

The system is **deterministic + override-driven** — you change outcomes by editing
small input files, not code. Common goals:

**"The auto-label for a word is wrong."** → add a line to `data/facet_overrides.tsv`:
```
exact:algorithm    CONCEPT          # mode:key  <tab> BUCKET [<tab> CUES]
normalized:due to  RELATION  CAUSE
```
`exact:` matches the surface as-is; `normalized:` matches the normalized form.
Overrides win over the heuristic (flag becomes MANUAL) and are reapplied every build.

**"A word's EPA feeling is off."** → EPA comes from the merged lexicon substrate
(Warriner + NRC). To correct one, add it to the manual layer / a higher-priority
source; the substrate's priority merge lets a trusted value override a weaker one.
(Don't edit the lexicon files in place — add an override source.)

**"I want search to find causal/conditional statements."** → make sure those words
carry the right cues (CAUSE, CONDITION, INFERENCE). The cue is what search keys on.

**"I want to strip filler before summarizing."** → rely on `utility=FILLER`; mark
any missed fillers via an override.

**"I want to separate finance vs medical vocabulary."** → that's the `domain` field
(fed from Wiktionary/source provenance). Populate the source so domain fills in.

**Open vs strict labeling.** When a word has multiple valid senses, prefer the
**open** choice — the union of cues, the more general bucket — so the token stays
usable in context (this is how we resolved the gold conflicts). Use a single narrow
label only when the word truly has one role.

**The review loop.** Anything `low` confidence or `needs_review` goes in the queue;
resolve it in the gold editor, and the accuracy harness re-scores. Adjust → re-run →
measure. Never hand-trust a number you haven't measured against gold.

---

## 5. How it all fits together

```
corpus → dictionary IDs → FACETS (rules) → META (richer fields) → EPA (lexicon match)
                                                           ↓
                         used by: compression, ID-space search, the verbalizer
```
Each layer is re-derived per build and fingerprinted, so a dictionary always
declares exactly which facets/meta/EPA produced it (the artifact-identity rule).

---

## 6. Cheat sheet

```
BUCKET   TOPIC concrete | CONCEPT abstract | METHOD action | RELATION glue/logic
         | STRUCTURAL punctuation | UNKNOWN filler
UTILITY  CONTENT | FUNCTION | STRUCTURAL | FILLER
CUES     CAUSE CONDITION INFERENCE EVIDENCE_CUE CONTRAST CONCESSION NEGATION
         QUANTIFIER MODAL QUESTION TEMPORAL COMPARISON DEFINITION_CUE CONJUNCTION CLAIM_CUE
FLAGS    MULTIWORD CLOSED_CLASS MANUAL HEURISTIC AMBIGUOUS
META     abstraction(concrete/abstract) causality temporality scope domain register
EPA      E good↔bad   P strong↔weak   A active↔passive    range ±4   0=neutral
READ     sign = pole, magnitude = intensity ;  |v|<0.5 neutral, 0.5–2 moderate, >2 strong
ADJUST   facet_overrides.tsv (labels) · higher-priority EPA source (feelings) ·
         cues drive search · utility drives keep/drop · domain drives subject routing
```

---

## 7. Pointers
```
Terms / record   spec-facets-db.md (facet) ; spec-meta-db.md (meta)
EPA              ../../Memory/mneme/docs/EPA.md ; spec-epa-expansion.md ; spec-epa-substrate.md
Labeling rubric  spec-facet-accuracy.md §3 ; gold: data/facet_gold*.tsv ; editor: facet_gold_editor.html
Overrides        data/facet_overrides.tsv
```
