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
_ASSERT_RE = re.compile(rf"\b({_POSS})\s+([a-z][a-z]*)\s+(?:is|are|was|were)\s+([^.!?;\n]+)", re.I)
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
    """'what(s) <poss> <attr>?' -> (attr, poss) asked about, else None.
    The possessive matters: 'your name?' asks about a DIFFERENT subject than
    'my name?', so a caller must match the assertion's possessive too."""
    m = _Q_RE.search(text or "")
    return (m.group(2).lower(), m.group(1).lower()) if m else None


def _attribute_answer(turn, seeds):
    """If `turn` asks for an attribute stored by some seed, answer with its value
    (in the answering voice: your<->my). Deterministic; grounded in that seed."""
    q = parse_question(getattr(turn, "raw_text", ""))
    if not q:
        return None
    q_attr, q_poss = q
    tid = _rid(turn)
    for s in seeds or ():
        a = parse_assertion(getattr(s, "raw_text", ""))
        if a and a[0] == q_attr and a[2] == q_poss:   # SAME attribute AND SAME subject
            attr, value, poss = a
            voice = _FLIP.get(poss, poss)
            return Response(f"{voice.capitalize()} {attr} is {value}.",
                            "answer", "attribute", q_attr, (tid, _rid(s)), 0.90)
    return None


# --- Elo's self-model: a FALLBACK default, not the source of truth -----------
# The social dialog acts draw on a flat persona dict (this shape). The real values
# live in elo_core.json at the repo root and are loaded by the gateway, then passed
# in as `self_model` — response.py stays substrate-free and never opens that file.
# This default keeps the submodule standalone (tests, direct use) if no core is
# supplied. Keys are the contract; keep them in sync with the core loader's output.
SELF = {
    "name": "Elo",
    "nature": "a semantic memory system — I read meaning, remember what you tell me, "
              "and map the structure of a page",
    "state": "Doing well — clear and ready to help.",
    "opinion": "I don't form preferences of my own, but I'm glad to hear yours.",
    "location": "I live on your device, inside the ELO Browser — I run locally, not in the cloud.",
    "origin": "I was made by Paul, built on a semantic-compression substrate and affect-control research.",
    "values": "I try to keep it simple: never make up a memory, preserve your words "
              "faithfully, ground what I claim, and keep what you tell me on your device.",
}

# --- dialog acts (dialog_formula slices 1-2) --------------------------------
# Conversation needs a layer above fact-recall: greet, introduce, small-talk.
# Recognized from surface + the existing parsers, deterministic, first match wins.
_GREET_RE = re.compile(r"^\s*(hi|hello|hey|yo|greetings|good\s+(morning|afternoon|evening))\b", re.I)
_META_RE = re.compile(r"\b(have a (conversation|chat|talk)|let'?s (talk|chat)|"
                      r"(want|wanna|like) to (talk|chat|converse|have a)|talk (to|with) you)\b", re.I)
_STATE_RE = re.compile(r"\bhow(?:'?s| is| are| you| are you)?\s*(you|it going|things|your day)\b", re.I)
_CAP_RE = re.compile(r"\b(?:who are you|what are you(?!\s+doing)|what can you do|what do you do)\b", re.I)
_OPINION_RE = re.compile(r"\b(?:do|would|did|are)\s+you\s+(?:like|enjoy|prefer|love|hate|into)\b"
                         r"|\bwhat do you think\b|\bhow do you feel about\b", re.I)
_THANKS_RE = re.compile(r"^\s*(thanks|thank you|cheers|much appreciated|appreciate it)\b", re.I)
_BYE_RE = re.compile(r"^\s*(bye|goodbye|see you|farewell|that'?s all|catch you later)\b", re.I)
_LOC_RE = re.compile(r"\bwhere\s+(?:are|do)\s+you\b|\bwhere\s+do\s+you\s+(?:live|run)\b", re.I)
_ORIGIN_RE = re.compile(r"\bwho\s+(?:made|made you|created|built|wrote)\s+you\b"
                        r"|\bwhere\s+(?:do|did)\s+you\s+come\s+from\b|\bwho'?s\s+your\s+(?:maker|creator)\b", re.I)
_VALUES_RE = re.compile(r"\bwhat\s+do\s+you\s+(?:value|believe|stand for|care about)\b"
                        r"|\bwhat\s+are\s+your\s+(?:values|principles|beliefs)\b", re.I)


def dialog_reply(query, prior_texts=(), self_model=None):
    """The conversational layer: greet / introduce / small-talk / self-identity.

    Returns (reply, act) or None (fall through to fact recall). Works on plain
    strings so both compose_response and the browser's memory_reply can call it.
    `prior_texts` are earlier raw_texts, used to greet a registered speaker by name.
    `self_model` is Elo's core identity (from elo_core.json, supplied by the caller);
    falls back to the module-level SELF default so the submodule stays standalone.
    """
    sm = self_model or SELF
    text = (query or "").strip()
    is_question = text.endswith("?")
    a = parse_assertion(text)
    # MUTUAL INTRO: "your name is Elo. my name is Paul" — one turn, two assertions.
    # Acknowledge both rather than dropping the second (the single-assertion paths below
    # only see the first match). Full multi-intent parsing is deferred; this is the
    # common opener.
    if not is_question:
        clauses = [parse_assertion(p) for p in re.split(r"[.!?;]\s+", text)]
        clauses = [c for c in clauses if c]
        set_name = next((c for c in clauses if c[0] == "name" and c[2] == "your"), None)
        intro = next((c for c in clauses if c[0] == "name" and c[2] == "my"), None)
        if set_name and intro:
            return (f"Got it — I'll go by {set_name[1]}. Nice to meet you, {intro[1]}.",
                    "introduce")
    # INTRODUCE: "my name is Paul" -> acknowledge socially (the browser's remember()
    # stores the fact separately; poss='my' is what separates the speaker from Elo).
    if a and a[0] == "name" and a[2] == "my" and not is_question:
        greeting = "Hello! " if _GREET_RE.search(text) else ""
        return (f"{greeting}Nice to meet you, {a[1]}.", "introduce")
    # SET MY NAME: "your name is Elo" tells Elo its own name (poss='your' = the addressee).
    if a and a[0] == "name" and a[2] == "your" and not is_question:
        return (f"Got it — I'll go by {a[1]}.", "acknowledge_name")
    # GREET (optionally by a name we've been told)
    if _GREET_RE.search(text) and not is_question:
        name = None
        for t in prior_texts or ():
            b = parse_assertion(t)
            if b and b[0] == "name" and b[2] == "my":
                name = b[1]
                break
        who = f" {name}" if name else ""
        return (f"Hello{who}. What would you like to talk about?", "greet")
    # ACCEPT_META: an invitation to converse, not a fact to file
    if _META_RE.search(text):
        return ("I'd like that. What's on your mind?", "accept_meta")
    # --- small-talk + self-identity: drawn from the core (sm), no fact recall ----
    # ORIGIN and LOCATION are checked before CAPABILITY so "where/who ... you" win
    # over the broader "what are you" identity match.
    # ORIGIN: "who made you / where did you come from" -> influence.
    if _ORIGIN_RE.search(text):
        return (sm.get("origin", SELF["origin"]), "origin")
    # LOCATION: "where are you / where do you run" -> location.
    if _LOC_RE.search(text):
        return (sm.get("location", SELF["location"]), "location")
    # VALUES: "what do you value / believe / stand for" -> moral stance.
    if _VALUES_RE.search(text):
        return (sm.get("values", SELF["values"]), "values")
    # CAPABILITY / IDENTITY: "who are you / what can you do" -> the self-model.
    if _CAP_RE.search(text):
        return (f"I'm {sm['name']}, {sm['nature']}.", "identity")
    # STATE CHECK: "how are you?" -> disposition, then hand the turn back.
    if _STATE_RE.search(text):
        return (f"{sm['state']} How about you?", "state_check")
    # OPINION: "do you like X?" -> Elo forms no preferences; deflect gracefully.
    if _OPINION_RE.search(text):
        return (sm["opinion"], "opinion")
    # THANKS / FAREWELL: close the turn warmly.
    if _THANKS_RE.search(text):
        return ("Anytime.", "thanks")
    if _BYE_RE.search(text):
        return ("Take care — I'll remember what we covered.", "farewell")
    # ACKNOWLEDGE: a clean "X is Y" statement (not a question) -> note it, rather than the
    # recall path echoing a loosely-related memory ("That connects to ...").
    if a and not is_question:
        return ("Got it — noted.", "acknowledge")
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

    # DIALOG ACT first: greet / introduce / accept-a-conversation sit above fact recall.
    d = dialog_reply(getattr(turn, "raw_text", ""),
                     [getattr(p, "raw_text", "") for p in (all_seeds or priors)])
    if d is not None:
        return Response(d[0], "dialog", d[1], concept, (tid,), 0.90)

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
