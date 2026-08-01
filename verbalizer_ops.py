"""
verbalizer_ops.py -- the three tier-2 verbalize ops, packaged.

`label`, `summarize`, `verbalize`: pure JSON-in/JSON-out functions the browser can
port against a conformance file (see VERBALIZER_OPS_SPEC.md). They ABSORB the
`response.py` composition ladder rather than duplicate it -- imported here via the
sc bare-import convention. No LLM, no faiss/LMDB/numpy at the contract boundary;
tier-1 reads (facet utility) degrade EXPLICITLY, never silently.

Standing rule: generation READS the store, never writes it. An op's output is never
a memory. Every emitted sentence cites what it stood on (`grounded_on` / `basis`).
"""
from __future__ import annotations

from collections import Counter

# Absorb the ladder (bare imports, sc convention). response.py is pure + substrate-
# free and guards its own facet import, so importing it never requires the substrate.
from response import (                                    # noqa: E402
    Shape, cue_read, dialog_reply, parse_assertion, parse_question, _FLIP,
    shape_grounded_reply, shape_unknown_reply, TEMPLATES,
)


# ===========================================================================
# label(items, context?) -> {label, basis, grounded_on}                tier 1
# ===========================================================================
def label(items, context=None) -> dict:
    """A 1-4 word designator for a set of items. Content-vs-function comes from the
    facet utility bit (never a stoplist); a multiword content surface is preferred
    (buckets alone can't separate a topic from the words inside it). Beats
    topicTracker.labelTopic, which returns raw top-k terms incl. function words."""
    ids = [it.get("id") for it in (items or [])]
    surfaces = list((context or {}).get("surfaces") or [])
    if not surfaces:
        # DEGRADE (flagged by absence of facets, not silent): no encoder surfaces
        # supplied -> fall back to the items' own whitespace tokens. We do NOT
        # re-join phrases here (that is the encoder's job; re-tokenizing is the bug
        # that deleted every number, 2026-07-27).
        for it in (items or []):
            surfaces.extend((it.get("text") or "").split())

    read = cue_read(surfaces)
    if read:
        content = [s for s, _cues, util in read if util == 0]      # 0 = CONTENT
    else:
        content = surfaces          # facets unavailable -> cannot classify (degraded)

    label_str, basis = _pick_label(content)
    return {"label": label_str, "basis": basis, "grounded_on": ids}


def _pick_label(content):
    if not content:
        return "", []
    freq = Counter(c.lower() for c in content)
    multi = [c for c in content if " " in c]
    if multi:                                   # prefer a multiword content surface
        chosen = max(multi, key=lambda c: (freq[c.lower()], len(c)))
    else:                                       # most frequent; tie -> leftmost (stable)
        best = max(freq.values())
        chosen = next(c for c in content if freq[c.lower()] == best)
    return " ".join(chosen.split()[:4]), [chosen]


# ===========================================================================
# summarize(items, budget) -> {summary, sentences, dropped}            tier 0
# ===========================================================================
_KIND_RANK = {"fact": 0, "intent": 1, "answer": 1, "question": 2}


def summarize(items, budget) -> dict:
    """At most `budget` grounded sentences, most salient first (kind then order).
    Each sentence cites its source id. `dropped` is reported -- NO silent
    truncation. Pure over the given items; opens nothing. Numbers are content and
    are preserved verbatim."""
    items = list(items or [])
    budget = max(0, int(budget))
    order = sorted(range(len(items)),
                   key=lambda i: (_KIND_RANK.get((items[i].get("kind") or "").lower(), 5), i))
    keep = sorted(order[:budget])               # emit in original order
    sentences = [{"text": _as_sentence(items[i].get("text") or ""),
                  "grounded_on": [items[i].get("id")]} for i in keep]
    return {"summary": " ".join(s["text"] for s in sentences),
            "sentences": sentences,
            "dropped": max(0, len(items) - budget)}


def _as_sentence(t: str) -> str:
    t = (t or "").strip()
    if not t:
        return t
    t = t[0].upper() + t[1:]
    if t[-1] not in ".!?":
        t += "."
    return t


# ===========================================================================
# verbalize(input) -> {text, stance_marks, grounded_on}              tier 0-1
# ===========================================================================
def verbalize(inp) -> dict:
    """Structured state -> composed sentences, in the SHAPE of what was asked,
    citing seeds, marking stance. The unification of the ladder. Resolution order
    (first match wins, all deterministic):

        1. social act (greet/introduce/self-identity)   -- needs `query`
        2. attribute answer (voice-flipped your<->my)    -- needs `query`
        3. grounded, in shape (recall found a seed)
        4. gap, in shape (recall empty)

    `query` (the raw asking turn) is optional; without it, verbalize is the shaped
    composer over `shape` + `seeds`. Stance is enforced: a told seed speaks in told
    voice, an inferred seed in inferred voice (hedged, cited) -- no template
    manufactures a reasoning step from a told fact (that is R1's job)."""
    inp = inp or {}
    seeds = list(inp.get("seeds") or [])
    shape = _shape_from(inp.get("shape"))
    intent = inp.get("intent")
    answered = inp.get("answered")
    query = inp.get("query")

    # 1-2. social / attribute -- only when the raw asking turn is supplied
    if query:
        social = dialog_reply(query, [(s.get("text") or "") for s in seeds])
        if social is not None:
            return _out(social[0], [], [], "social")
        aq = parse_question(query)
        if aq:
            q_attr, q_poss = aq
            for s in seeds:
                a = parse_assertion(s.get("text") or "")
                if a and a[0] == q_attr and a[2] == q_poss:
                    _attr, value, poss = a
                    text = f"{_FLIP.get(poss, poss).capitalize()} {_attr} is {value}."
                    return _out(text, [(0, len(text), "told")], [s.get("id")], "attribute")

    # 4. gap, in shape -- the gateway keys _wonder_on_gap on basis=='gap'
    if not seeds:
        fallback = TEMPLATES["unknown"][0].format(concept=(shape.subject or "that"))
        return _out(shape_unknown_reply(shape, fallback), [], [], "gap")

    # 3. grounded, in shape -- compose the salient recalled seed
    top = seeds[0]
    fact = (top.get("text") or "").strip()
    stance = (top.get("stance") or "told").lower()

    if stance == "told":
        fallback = f'From what you told me: "{fact.rstrip(".")}".'
        body = shape_grounded_reply(shape, fact, fallback, intent=intent)
        # 'grounded' = composed into the shape; 'fallback' = no shape form applied,
        # the seed was quoted verbatim. The gateway may treat the quote differently.
        basis = "grounded" if body != fallback else "fallback"
    else:
        # inferred / speculation: hedged, inferred voice, cited. R1's licensing
        # lexicon (contributes_to/requires/...) refines this at integration.
        lead = "That would suggest" if stance == "inferred" else "One possibility"
        body = f'{lead}: {fact.rstrip(".")}.'
        basis = "grounded"

    prefix = ""
    if answered and answered.get("subject"):
        prefix = f"Noted on {answered['subject']}. "
    text = prefix + body
    return _out(text, [(len(prefix), len(text), stance)], [top.get("id")], basis)


def _shape_from(d) -> Shape:
    if isinstance(d, Shape):
        return d
    d = d or {}
    return Shape(d.get("shape", "statement"), frozenset(d.get("cues") or ()),
                 tuple(d.get("options") or ()), tuple(d.get("compared") or ()),
                 d.get("subject", ""))


def _out(text, marks, grounded_on, basis) -> dict:
    return {"text": text,
            "basis": basis,                       # social|attribute|grounded|gap|fallback
            "stance_marks": [{"span": [a, b], "stance": st} for (a, b, st) in marks],
            "grounded_on": list(grounded_on)}


# ===========================================================================
# stance mapping -- ONE spec'd place (A3). MemorySeed has no `stance`; the gateway
# and browser adapters map their local seed fields into it BEFORE calling the ops,
# and must use the identical mapping. This is that mapping, portable and pinned.
# ===========================================================================
def stance_from_seed(claim_type=None, memory_type=None, certainty=None,
                     from_reasoning=False) -> str:
    """Map a stored seed's fields -> stance ('told'|'inferred'|'speculation').

        from_reasoning (R1-derived provenance)      -> inferred
        certainty < 0.35, or memory_type speculative -> speculation
        else (a plain asserted fact)                 -> told

    told = the user said it; inferred = a licensed reasoning step produced it
    (cite the step); speculation = low-confidence, hedged. Voice must match."""
    if from_reasoning:
        return "inferred"
    if (memory_type or "").lower() in ("speculation", "speculative", "hypothesis"):
        return "speculation"
    try:
        if certainty is not None and float(certainty) < 0.35:
            return "speculation"
    except (TypeError, ValueError):
        pass
    return "told"
