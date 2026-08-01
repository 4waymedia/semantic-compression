# Verbalizer Ops — Integration Notes (deliverable d)

> Where `label` / `summarize` / `verbalize` (see `VERBALIZER_OPS_SPEC.md`,
> reference `verbalizer_ops.py`, contract `verbalizer_conformance.json`) slot into
> the existing call sites — what each maps to, and what gets **deleted**. The
> theme: the gateway already hand-wires the ladder piece by piece; these ops
> **collapse** those branches into one call. No behavior invented here — every
> target is pinned by the conformance file.

---

## 1. Gateway — the composition ladder in `_memory_reply_core` → one `verbalize` call

**Location (corrected, A2):** post the P1 split, the composition ladder lives in
`08-MCP-ToolInterface/mcp_tools/tool_api.py::_memory_reply_core`. `memory_reply` is
the **conversation wrapper** (dialog-act exclusion, slot capture, `memory_count` /
`answered_question` / `nudge`) — this landing must **not touch it**. Only the core's
composition branches collapse:

| current call site (in `_memory_reply_core`) | maps to `verbalize` step / `basis` |
|---|---|
| `dialog_reply(query, self_model=…)` | 1. social act → `basis:"social"` |
| `_attribute_answer` path | 2. attribute answer → `basis:"attribute"` |
| `read_shape` + `shape_grounded_reply(…, intent=…)` | 3. grounded → `basis:"grounded"` (or `"fallback"` when quoted) |
| `read_shape` + `shape_unknown_reply(…)` | 4. gap → `basis:"gap"` |

**Replace** the composition with one call — and **preserve the wire contract (A2)**.
`memory_reply`'s return is a protocol the browser branches on; the adapter maps
`basis→act` and passes `shape` through as today (only when it changed the reply),
and leaves the conversation-aware wrapper fields untouched:

```python
from verbalizer_ops import verbalize, stance_from_seed
result = verbalize({
    "query":    query,                       # enables social + attribute (steps 1-2)
    "shape":    shape.to_dict(),             # gateway still computes read_shape(surfaces)
    "seeds":    [{"id": s.id, "kind": s.kind, "text": s.text,
                 "stance": stance_from_seed(s.claim_type, s.memory_type, s.certainty,
                                            from_reasoning=s.from_r1)} for s in recalled],
    "intent":   held_intent_text,            # from the gateway's _INTENT_RE (A4)
    "answered": captured_answer,
})
# --- adapter: op output -> wire protocol (do NOT drop these) ---
reply       = result["text"]
grounded_on = result["grounded_on"]
act         = _BASIS_TO_ACT.get(result["basis"])   # social basis -> dialog act (circle-back guard)
# 'shape', 'memory_count', 'answered_question', 'nudge' set by the wrapper as today;
# pass 'shape' through only when the op used it (basis in {grounded, gap}).
if result["basis"] == "gap":
    _wonder_on_gap(shape, query)             # gateway verb stays gateway-side
```

**Deleted:** only the branch-by-branch composition inside `_memory_reply_core` and
its local quoting-fallback assembly. **Not deleted / not touched:** `memory_reply`
the wrapper, the `act`/`shape`/`memory_count`/`answered_question`/`nudge` fields,
`read_shape` (stays in the gateway — the op takes its *output*, never re-tokenizes).

> The gateway keeps *recall*, *encoding* (`read_shape`), and its *verbs*
> (`_wonder_on_gap`, slot capture), reading `verbalize`'s `basis` to know which
> fired. `verbalize` owns only *composition*. Substrate on the gateway side, pure
> composition on the op side.

### 1a. Two guards stay gateway-side (A4)

The op receives **clean** inputs and must not re-implement or assume them:

- **Recall hygiene** — wonder-question / request filtering ("a recall is not an
  answer") runs before the op sees `seeds`. The op trusts the seed set it is given.
- **Intent extraction** — `_INTENT_RE` over the recalled set produces the `intent`
  string. The op consumes `intent`; it does not scan for it.

---

## 2. Gateway — `memory_verbalize` → thin wrapper over `summarize`

`tool_api.py::memory_verbalize` (line 641) becomes:

```python
from verbalizer_ops import summarize
def memory_verbalize(self, query, entity="self", budget=3):
    seeds = self._recall(query, entity)      # unchanged: recall stays substrate-side
    return summarize([{"id": s.id, "kind": s.kind, "text": s.text} for s in seeds],
                     budget)
```

**Deleted:** any bespoke truncation/joining. `summarize` reports `dropped`
(no silent truncation) and cites each sentence — the gateway just forwards.

> **Caution:** `memory_verbalize`'s current return shape may have consumers
> (`eloAi.verbalize`). Check them before slimming to the bare `summarize` output —
> keep a compatibility wrapper if the shape is depended on.

---

## 3. Browser — `lib.rs::respond()` → ported ops over local recall

`ELO-Browser/elo-browser/src-tauri/src/lib.rs::respond()` emits an affect note
today ("On dungeon, master — reads neutral"). STANDALONE voice = call the **ported**
`verbalize` over the local seed store:

1. recall seeds from `seeds.jsonl` (L0 exact-surface — already present),
2. compute `shape` from the turn's encoder surfaces (port `read_shape` — it reads
   the `facets.bin` cue mask the browser already loads),
3. `verbalize({query, shape, seeds, intent, answered})` → the reply.

The port is proven against `verbalizer_conformance.json` (the JS/Rust runner is the
second runner; this repo's `test_verbalizer_conformance.py` is the first). Fix the
`whats`-topic bug in the same pass — `read_shape` reads the cue mask, not
frequency, so the interrogative no longer wins.

**Keep the EPA affect read as *tone*, don't delete it.** The original design pairing:
**shape picks the frame, affect colors it.** `respond()`'s EPA read stays as tone
input alongside `verbalize`'s shaped composition — the affect note is only *replaced*
as the reply floor for turns with a store hit, not removed.

**Deleted:** the affect-only reply floor for turns that have a store hit (affect
persists as tone, above).

---

## 4. Conversation layer — inputs already computed

`Shape`, captured answers, and held intents are computed **per turn** by the
conversation layer today; they flow straight into `verbalize` as `shape` /
`answered` / `intent`. No new computation — just routing existing values into the
op's input.

---

## 5. Formula runtime — the closed op table

`label`, `summarize`, `verbalize` register as tier-2 ops in the runtime's closed op
table (registry + engine, separate session). Their browser tier is declared
(spec §4): `summarize` tier-0, `label`/`verbalize` tier-1 (facet read upstream).
This spec defines the ops; the engine executes them.

---

## 6. Order of landing (each shippable)

1. **Gateway `memory_reply`** → `verbalize` (biggest win; `read_shape` already
   present there). Validate against the reply-shape probes
   (`08-MCP.../tests/reply_shape_probes.py`) — must beat the quoting fallback.
2. **Gateway `memory_verbalize`** → `summarize` wrapper.
3. **Browser `respond()`** → ported ops (needs the `read_shape` port + `verbalize`
   port; conformance file is the certificate).
4. **Formula runtime** registration when that engine lands.

## 7. Graduation (bookkeeping)

`verbalizer_ops.py` + probes + `verbalizer_conformance.json` stay **canonical in the
working tree** (`semantic_compression/`) until the browser port passes conformance.
Then a **sync** step (not a fork) copies the matured module into
`packages/eloai-verbalizer/` with the conformance file as its certificate — two live
copies of composition logic is the drift this project keeps paying to prevent. One
registry pass across `packages/*` is worth doing then, to record which packages
exist and what stage their contents are at (three sessions are packaging in
parallel).
