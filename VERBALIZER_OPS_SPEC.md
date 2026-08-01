# Verbalizer Ops — Contract Spec (`label` · `summarize` · `verbalize`)

> **Status: SPEC (deliverable a of the Verbalizer Ops handoff, 2026-08-01).**
> The three tier-2 verbalize ops packaged as portable, conformance-pinned pure
> functions the browser can wire in. Reference implementation lands alongside
> `response.py` (deliverable b); the conformance JSON is generated from it
> (deliverable c); integration notes map the call sites (deliverable d).
>
> Companions: `ELO_SYSTEMS_ALIGNMENT.md`, `ELO_SEED_STORE_SPEC.md`,
> `ELO_REBUILD_SPEC.md`. Absorb, don't duplicate — §7 maps every op to the
> `response.py` ladder it stands on.

---

## 0. Why these three

The browser can perceive, remember, recall, and track a conversation; it cannot
**say what it knows**. Its local reply floor today is an affect note ("On dungeon,
master — reads neutral") while holding a taught paragraph in its store. `label`,
`summarize`, and `verbalize` are the voice. When they land, generative formulas
unblock and STANDALONE mode speaks — composed, cited, stance-marked, offline.

`verbalize` is the target; `label` and `summarize` are its warm-ups and share
internals (subject selection, provenance, degradation).

---

## 1. The packaging contract (what "wirable" means)

1. **Deterministic, no LLM, dictionary-grounded.** No sampling, no model call.
   Standing rule: **generation reads the store, never writes it** — an op's output
   is never remembered as knowledge.
2. **Substrate-light inputs only.** Each op runs on what the *browser* can supply:
   encoder **surfaces** (learned phrases pre-joined — never re-tokenize), **facet
   reads** (bucket / cue names / utility bit), **EPA** values, **seeds** as
   `{id, kind, text}`, topic-thread **labels**, and the conversation layer's
   **`Shape`**. No faiss, no LMDB, no numpy at the contract boundary. A reference
   impl may use substrate internally; the *contract* must not — and it degrades
   **explicitly**, never silently (miner-hygiene rule).
3. **Closed, pure functions.** Typed JSON in → typed JSON out. No hidden state, no
   side effects. Every op declares its browser tier (0 local-deterministic, 1
   substrate/edge). Signatures (§3) are the contract; keep them JSON-serializable.
4. **Provenance on every output.** Composed language cites the seed ids and
   surfaces it stood on (`grounded_on`). *A sentence the op cannot cite is a
   sentence it does not emit.*
5. **Three artifacts cross the boundary:** (a) this spec, (b) the Python reference
   in R-D with probes green, (c) the conformance JSON generated from the reference.
   The browser port (JS/Rust) is proven against the conformance file — same pattern
   as `discourse/conversation_conformance.json`. **R-D builds; the browser proves;
   the port never invents.**

---

## 2. Shared types (JSON)

```
Seed    = { "id": str, "kind": str, "text": str, "stance"?: "told"|"inferred"|"speculation" }
            # kind ∈ store's seed kinds (fact/intent/question/...); stance defaults "told".
Shape   = { "shape": str, "cues": [str], "options": [str], "compared": [str], "subject": str }
            # produced by read_shape() from the asking turn's encoder surfaces (§7).
Context = { "surfaces": [str] }                        # encoder surfaces, phrases pre-joined
Answered= { "question": str, "subject": str }          # a just-captured answer to weave in
StanceMark = { "span": [int,int], "stance": "told"|"inferred"|"speculation" }
```

`stance` is first-class because §5.5 forbids saying an inferred thing in a told
voice. `told` = the user said it; `inferred` = an R1 step licensed it (cite the
step); `speculation` = explicitly hedged. Voice must match stance.

---

## 3. Op signatures + semantics

### 3.1 `label(items, context?) -> { label, basis, grounded_on }`  — tier 1

```
label(items: [{id, text}], context?: {surfaces:[...]})
  -> { "label": str,               # 1–4 word designator
       "basis": [surface, ...],    # the surfaces that earned it
       "grounded_on": [id, ...] }  # the items it labels
```

Name a set of items with a short designator. **Content-vs-function comes from the
facet utility bit, never a stoplist** (§5.2). Prefer a **multiword** content
surface when present (the `read_shape` subject tiebreak: buckets alone can't
separate a topic from the words inside it). Ties broken by frequency then leftmost.
`basis` is the surfaces chosen; `grounded_on` is every item id covered. Must beat
`topicTracker.labelTopic` (top-k SVD terms like "wash drive walk told") on its own
examples — scored, not asserted.

Degrade: no facets → `basis` from the raw content surfaces by frequency; still
returns a label, flagged in the probe as degraded (never a silent stopword list).

### 3.2 `summarize(items, budget) -> { summary, sentences, dropped }`  — tier 0

```
summarize(items: [{id, kind, text}], budget: int /* sentences */)
  -> { "summary": str,
       "sentences": [{ "text": str, "grounded_on": [id, ...] }],
       "dropped": int }            # count omitted — NO silent truncation
```

Reduce items to at most `budget` grounded sentences. Selection by salience (kind
priority then order; `fact` and `intent` outrank filler). Each emitted sentence
cites its source id(s). `dropped = max(0, len(items) - budget)` — the caller always
learns what was cut (§5.7). `summary` is the sentences joined. Pure over the given
items; opens nothing.

### 3.3 `verbalize(input) -> { text, stance_marks, grounded_on }`  — tier 0–1

```
verbalize(input: {
    shape?:    Shape,                 # discourse shape of the asking turn
    seeds:     [Seed],                # recalled seeds (with stance)
    intent?:   str,                   # a held intent seed's text, if any
    answered?: Answered,              # a just-captured answer to weave in
  })
  -> { "text": str,
       "stance_marks": [StanceMark],  # told vs inferred visibly distinct
       "grounded_on": [id, ...] }
```

Structured state → composed sentences, in the **shape** of what was asked, citing
the seeds, marking stance. The unification of the ladder (§7). Order of resolution
(first that applies wins, all deterministic):

1. **Social act** — greet / introduce / self-identity / accept-a-conversation
   (`dialog_reply`). No seeds needed; `grounded_on = []`.
2. **Attribute answer** — the turn asks for a stored attribute of a subject
   (`_attribute_answer`, voice-flipped `your↔my`). Cites the asserting seed.
3. **Grounded, in shape** — recall found a seed: compose it into the shape
   (`shape_grounded_reply`) — modal-choice names the options and the open part,
   comparison names both sides, definition distinguishes *mention* from
   *definition*, the intent guard cites a held intent instead of denying it.
4. **Gap, in shape** — recall empty: name what was asked (`shape_unknown_reply`)
   rather than the generic "nothing stored."

**Stance is enforced, not decorative.** A `told` seed is said in told voice ("You
told me: …"); an `inferred` seed is said in inferred voice ("that would suggest …")
and its `grounded_on` includes the R1 step id; `speculation` is explicitly hedged.
`stance_marks` records the char spans so the browser can render told/inferred
distinctly. **No template may smuggle a reasoning step** (§5.5): inference is said
only when an `inferred` seed carries it, licensed and cited — never manufactured
from a `told` fact.

---

## 4. Browser-tier ledger

| op | tier | needs | degrades to |
|---|---|---|---|
| `label` | 1 | facet utility bit (for content-vs-function) | frequency over raw surfaces, flagged |
| `summarize` | 0 | nothing beyond the items | n/a (pure) |
| `verbalize` | 0–1 | `Shape` (tier-1 facet read, upstream); composition is tier 0 | no-shape → `shape='statement'`, generic reply |

All three are within the browser's reach: it holds the seed store, the encoder
surfaces, and the facet channel (`facets.bin`). None require faiss/LMDB/numpy.

---

## 5. Invariants (each cost a live bug — the ops must honor them)

1. **No punctuation dependence.** Questions/shapes are detected structurally (cue
   mask), never by `?`. (`verbalize` may accept a trailing-`?` hint but must not
   *require* it.)
2. **Dictionary verdicts over hand lists.** Content-vs-function is the facet
   utility bit. A 41-word stoplist once made recall answer "should I drive or
   walk?" with "free from dirt — means there is no dirt."
3. **Never claim ignorance while holding the answer.** The intent guard: cite a
   held intent instead of "nothing I have stored says how you weigh that."
4. **Mention ≠ definition.** A seed containing the words does not define the
   concept (`definition_mention`).
5. **Never fake inference.** No template smuggles a reasoning step; inference is
   R1's job — licensed, cited, said in inferred voice — or it is not said.
6. **Numbers are content.** Never drop them; a second tokenizer once silently
   deleted every number.
7. **Measured, not predicted.** Baseline probes before, scored probes after. A
   green run must be meaningful: substrate-missing cases **SKIP**, never PASS.
8. **Comments say why, with dated live evidence.** Every rule carries its
   transcript.

---

## 6. Provenance & the "reads, never writes" rule

- Every emitted sentence carries `grounded_on` (seed ids) and, for `label`,
  `basis` (surfaces). Empty grounding is allowed only for pure social acts (greet),
  which invent no knowledge.
- Ops are read-only over the store. The caller may choose to store the *turn*, but
  never the op's *output* — generated language is not a memory.

---

## 7. What each op absorbs (map to `response.py`)

| op | absorbs | notes |
|---|---|---|
| `label` | `read_shape` subject tiebreak (multiword content), `cue_read` (utility bit) | replaces/one-ups `topicTracker.labelTopic` |
| `summarize` | seed `{id,kind,text}` + provenance convention (`grounded_on`) | new selection layer; reuses the citation shape |
| `verbalize` | `dialog_reply` (social), `_attribute_answer` + `parse_assertion`/`parse_question` (voice flip), `read_shape`/`Shape`, `shape_grounded_reply`/`GROUNDED_TEMPLATES` (intent guard, definition_mention), `shape_unknown_reply`/`SHAPE_TEMPLATES`, `compose_response`/`TEMPLATES` | `compose_response`/`TEMPLATES` is the proto-verbalize with almost no callers — this is the work that gives it one |

Mine but do **not** bind to: the 06 verbalizer (`recall_field`/`expand`) and
`memory_verbalize` (substrate-heavy — semantics only); R1's trace vocabulary
(`contributes_to`/`requires`/`evidence_for` + confidence ceilings) defines the
inferred-voice lexicon `verbalize` must eventually speak.

---

## 8. Deliverable / graduation plan

1. **This spec** — signatures + semantics + tier ledger + invariants. ✅ (a)
2. **Reference impl** alongside `response.py` (`verbalizer_ops.py`), pure
   JSON-in/out, with probe suites: baseline-vs-after where an op replaces existing
   behavior (`label` beats `labelTopic`; `verbalize` beats the quoting fallback on
   the reply-shape probes). (b)
3. **Conformance JSON** generated from the reference — ≥10 cases per op **including
   degradation** (empty seeds, no shape, budget 1). This file is the contract and
   the graduation certificate. (c)
4. **Integration notes** — exact call sites: `memory_reply`'s composer → `verbalize`;
   `memory_verbalize` → thin wrapper over `summarize`; `lib.rs respond()` over local
   recall; conversation-layer `Shape`/answers/intents flow in as `verbalize` inputs.
   What each template branch maps to; what gets deleted. (d)

## 9. Out of scope (hard)

Any LLM. Any new tokenizer. Any hand-authored stopword/vocabulary list. Any op
requiring faiss / LMDB / numpy at the contract boundary. The formula-runtime
executor (separate session) — these ops slot into its closed op table; this spec
defines the ops, not the engine.

## 10. The bar

```
You: do you know what a dungeon master is?
ELO: From what you taught me: a Dungeon Master is the organizer and referee of a
     Dungeons & Dragons game — the one who runs everything except the players' own
     characters.  (grounded on 1 seed, told)
```

Composed, cited, stance-marked, offline — browser alone, MCP off. That is what
`verbalize` makes true.
