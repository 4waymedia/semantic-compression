# Verbalizer Ops — Integration Notes (deliverable d)

> Where `label` / `summarize` / `verbalize` (see `VERBALIZER_OPS_SPEC.md`,
> reference `verbalizer_ops.py`, contract `verbalizer_conformance.json`) slot into
> the existing call sites — what each maps to, and what gets **deleted**. The
> theme: the gateway already hand-wires the ladder piece by piece; these ops
> **collapse** those branches into one call. No behavior invented here — every
> target is pinned by the conformance file.

---

## 1. Gateway — `memory_reply` → one `verbalize` call

`08-MCP-ToolInterface/mcp_tools/tool_api.py::memory_reply` (line 736) currently
assembles a reply by calling the ladder members directly and in sequence:

| current call site | maps to `verbalize` step |
|---|---|
| `dialog_reply(query, self_model=…)` — line 807/812 | 1. social act |
| `_attribute_answer` path — line 905 | 2. attribute answer |
| `read_shape(surfaces)` + `shape_grounded_reply(sh, text, reply, intent=…)` — line 940–948 | 3. grounded, in shape |
| `read_shape(surfaces)` + `shape_unknown_reply(sh, generic)` — line 863–866 | 4. gap, in shape |
| the quoting fallback ("From what you have told me: …") — line 177 note | the `fallback` arg inside verbalize; no longer assembled here |

**Replace** that whole sequence with a single call:

```python
from verbalizer_ops import verbalize
result = verbalize({
    "query":    query,                       # enables social + attribute (steps 1-2)
    "shape":    read_shape(surfaces).to_dict(),   # gateway still owns the encoder surfaces
    "seeds":    [{"id": s.id, "kind": s.kind, "text": s.text,
                 "stance": s.stance} for s in recalled],
    "intent":   held_intent_text,            # if a held intent seed exists
    "answered": captured_answer,             # {question, subject} if this turn answered one
})
reply, grounded_on = result["text"], result["grounded_on"]
```

**Deleted:** the branch-by-branch `if shape == … / elif …` composition, the
duplicate `read_shape` imports scattered through the function, and the local
quoting-fallback assembly. `verbalize` owns the order-of-resolution (§3.3 of the
spec) and the stance marks. `read_shape` stays in the gateway (it needs the
encoder's surfaces); its **output** flows in as the `shape` field — the op never
re-tokenizes.

> The gateway keeps ownership of *recall* (which seeds) and *encoding* (surfaces /
> `read_shape`); `verbalize` owns *composition*. Clean seam: substrate on the
> gateway side, pure composition on the op side.

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

**Deleted:** the affect-only reply floor for turns that have a store hit.

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
