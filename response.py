"""
response.py -- Verbalizer (System 2) response composer.

The verbalizer turns internal state into language. Its highest-level function is a
*response*: given the latest turn plus the memory around it, compose one sentence
that relates the turn to what is already known -- a reaction to a statement, or an
answer to a question. Deterministic, no LLM. This is the pre-built-response
foundation an AI can respond from, and each Response can later be emitted as a seed
for the internal loops (Wonder/Reflection) to explore.

Pure + substrate-free: it duck-types seeds (needs `id`, `concept_id`, `raw_text`,
optionally `vec4d`) and takes contradictions as data, so it composes without opening
the dictionary and unit-tests with plain objects. Callers (PipelineLab, the runtime)
supply the memory context (priors on the concept + which seed-pairs contradict,
from the 06 graph).

Templates + confidences live in TEMPLATES -- tunable, like R1's LICENSE table.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Tuple

# basis -> (template, base_confidence). Straight ASCII for test/console safety.
TEMPLATES = {
    # reaction to a statement
    "contradiction": ('That conflicts with an earlier statement: "{ref}".', 0.70),
    "agreement":     ('That is consistent with an earlier point about {concept}: "{ref}".', 0.60),
    "novelty":       ("Noted: a new point about {concept}.", 0.40),
    # answer to a question
    "conflict":      ('There is a conflict about {concept}: "{a}" vs "{b}".', 0.70),
    "recall":        ("From memory about {concept}: {joined}.", 0.60),
    "unknown":       ("I have nothing stored about {concept} yet.", 0.30),
}


@dataclass(frozen=True)
class Response:
    text: str
    kind: str            # 'reaction' | 'answer'
    basis: str           # contradiction|agreement|novelty | conflict|recall|unknown
    concept: str
    references: tuple = ()   # seed ids the response is grounded in
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {"text": self.text, "kind": self.kind, "basis": self.basis,
                "concept": self.concept, "references": list(self.references),
                "confidence": self.confidence}


# -- duck-typed seed accessors ----------------------------------------------
def _rid(s):
    return getattr(s, "id", None)


def _concept(s) -> str:
    return (getattr(s, "concept_id", "") or "").lower()


def _text(s) -> str:
    return (getattr(s, "raw_text", "") or "").strip().rstrip(".")


def _is_question(s) -> bool:
    return (getattr(s, "raw_text", "") or "").rstrip().endswith("?")


def _contra(pairs, a, b) -> bool:
    return frozenset((a, b)) in pairs


def _first_conflict(priors, pairs):
    for i, a in enumerate(priors):
        for b in priors[i + 1:]:
            if _contra(pairs, _rid(a), _rid(b)):
                return (a, b)
    return None


# -- attribute / identity layer ---------------------------------------------
# "your name is Elo" / "my deadline is Friday" — a statement asserts attribute =
# value; a question asks for the attribute. These must meet on the ATTRIBUTE, which
# concept-grouping can't guarantee (the statement often keys on the value: "Elo").
# So we match the pattern directly, across all seeds, and answer with the value.
_POSS = r"(?:my|your|his|her|its|their|our|the)"
_ASSERT_RE = re.compile(rf"\b({_POSS})\s+([a-z][a-z]*)\s+(?:is|are|was|were)\s+(.+)", re.I)
_Q_RE = re.compile(rf"\bwhat(?:'?s| is| are)?\s+({_POSS})\s+([a-z]+)", re.I)
_FLIP = {"your": "my", "my": "your"}


def parse_assertion(text):
    """'<poss> <attr> is <value>' -> (attr, value, poss), else None."""
    m = _ASSERT_RE.search(text or "")
    if not m:
        return None
    poss, attr = m.group(1).lower(), m.group(2).lower()
    value = m.group(3).strip().rstrip(".!?").strip()
    return (attr, value, poss) if value else None


def parse_question(text):
    """'what(s) <poss> <attr>?' -> the attribute asked about, else None."""
    m = _Q_RE.search(text or "")
    return m.group(2).lower() if m else None


def _attribute_answer(turn, seeds):
    """If `turn` asks for an attribute stored by some seed, answer with its value
    (in the answering voice: your<->my). Deterministic; grounded in that seed."""
    q_attr = parse_question(getattr(turn, "raw_text", ""))
    if not q_attr:
        return None
    tid = _rid(turn)
    for s in seeds or ():
        a = parse_assertion(getattr(s, "raw_text", ""))
        if a and a[0] == q_attr:
            attr, value, poss = a
            voice = _FLIP.get(poss, poss)
            return Response(f"{voice.capitalize()} {attr} is {value}.",
                            "answer", "attribute", q_attr, (tid, _rid(s)), 0.90)
    return None


def compose_response(turn, priors: Iterable, contradiction_pairs=frozenset(),
                     all_seeds=None) -> Response:
    """Compose a response to `turn` (the latest seed) from `priors` (seeds sharing
    its concept). `contradiction_pairs` is a set of `frozenset({id_a, id_b})` that
    conflict (from the 06 CONTRADICTS graph). `all_seeds` (optional) is every other
    stored seed, used for attribute/identity answers where the concept may differ.
    A question (trailing '?') is answered; otherwise the turn is reacted to."""
    concept = _concept(turn) or "this"
    tid = _rid(turn)
    priors = [p for p in priors if _rid(p) != tid]
    pairs = frozenset(contradiction_pairs)

    if _is_question(turn):
        pool = [s for s in (all_seeds if all_seeds is not None else priors)
                if _rid(s) != tid]
        attr = _attribute_answer(turn, pool)     # identity/facts take priority
        if attr is not None:
            return attr
        return _answer(tid, priors, concept, pairs)
    return _react(turn, tid, priors, concept, pairs)


def _react(turn, tid, priors, concept, pairs) -> Response:
    contra = next((p for p in priors if _contra(pairs, tid, _rid(p))), None)
    if contra is not None:
        tpl, conf = TEMPLATES["contradiction"]
        return Response(tpl.format(ref=_text(contra), concept=concept),
                        "reaction", "contradiction", concept, (tid, _rid(contra)), conf)
    if priors:
        tpl, conf = TEMPLATES["agreement"]
        return Response(tpl.format(ref=_text(priors[-1]), concept=concept),
                        "reaction", "agreement", concept, (tid, _rid(priors[-1])), conf)
    tpl, conf = TEMPLATES["novelty"]
    return Response(tpl.format(concept=concept), "reaction", "novelty", concept, (tid,), conf)


def _answer(tid, priors, concept, pairs) -> Response:
    if not priors:
        tpl, conf = TEMPLATES["unknown"]
        return Response(tpl.format(concept=concept), "answer", "unknown", concept, (tid,), conf)
    pair = _first_conflict(priors, pairs)
    if pair:
        a, b = pair
        tpl, conf = TEMPLATES["conflict"]
        return Response(tpl.format(concept=concept, a=_text(a), b=_text(b)),
                        "answer", "conflict", concept, (_rid(a), _rid(b)), conf)
    joined = "; ".join(f'"{_text(p)}"' for p in priors if _text(p))
    tpl, conf = TEMPLATES["recall"]
    return Response(tpl.format(concept=concept, joined=joined),
                    "answer", "recall", concept, tuple(_rid(p) for p in priors), conf)
