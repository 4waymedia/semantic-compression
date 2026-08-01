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
_META_RE = re.compile(r"\b(have a (conversation|chat|talk)|let'?s (talk|chat)|can we (talk|chat|converse)|"
                      r"(want|wanna|like) to (talk|chat|converse|have a)|talk (to|with) you)\b", re.I)
# permission-to-ask: "can/could/may I ask (you) (a) question(s)/something" -- a
# social move deserving an immediate yes, never a recall round-trip (2026-08-01).
_ASKPERM_RE = re.compile(
    r"\b(?:can|could|may)\s+i\s+ask\s+(?:you\s+)?(?:a\s+|another\s+|some\s+|a\s+few\s+)?"
    r"(?:questions?|something|anything)\b", re.I)
_STATE_RE = re.compile(r"\bhow(?:'?s| is| are| you| are you)?\s*(you|it going|things|your day)\b", re.I)
_CAP_RE = re.compile(r"\b(?:who are you|what are you(?!\s+doing)|what can you do|what do you do)\b", re.I)
_OPINION_RE = re.compile(r"\b(?:do|would|did|are)\s+you\s+(?:like|enjoy|prefer|love|hate|into)\b"
                         r"|\bwhat do you think\b|\bhow do you feel about\b"
                         r"|\bdoes\s+(?:this|that|it)\s+"
                         r"(?:excite|interest|bother|please|bore|worry)\s+you\b"
                         r"|\bare\s+you\s+(?:excited|interested|curious|happy|bored)\b", re.I)
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
    if _ASKPERM_RE.search(query or ""):
        return ("Yes — ask away.", "accept_meta")
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
    # BUT 'acknowledge' (a bare "X is Y" note, "Got it -- noted.") must NOT preempt the
    # reaction path: contradiction / agreement / novelty are the informative reply for a
    # statement, and a statement goes through _react (not the recall echo the acknowledge
    # branch was added to avoid). Regression found 2026-08-01: the acknowledge act
    # shadowed contradiction detection (6 test_response failures). Let social acts win;
    # let acknowledge fall through to _react, whose 'novelty' already says "Noted:".
    d = dialog_reply(getattr(turn, "raw_text", ""),
                     [getattr(p, "raw_text", "") for p in (all_seeds or priors)])
    if d is not None and d[1] != "acknowledge":
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


# ---------------------------------------------------------------------------
# DISCOURSE SHAPE -- reading the cue mask that facets.bin already ships
# ---------------------------------------------------------------------------
# The reply path chose between two templates on `query.endswith("?")`. The
# dictionary has carried a richer read the whole time: `assign_facet` returns a
# 16-bit `cue_mask` naming the reasoning role of 304 curated surfaces
# (config.LOGIC_SEED_LISTS) -- CONDITION, MODAL, COMPARISON, NEGATION, CAUSE,
# QUESTION and the rest. Nothing consumed it. `cognition()` in the browser built
# a cue histogram and formatted it into a diagnostic string; the reply was
# composed on the other side of a process boundary and never saw one.
#
# This reads that mask and names the SHAPE of a turn. It does not know content:
# it cannot decide walk-vs-drive. It knows what KIND of thing was asked, which is
# what picks the reply form and, when recall is empty, what makes the gap
# specific instead of generic.
#
# The surfaces come from the CALLER's encoder, deliberately: the encoder already
# joins learned phrases, so 'car wash' arrives as one surface. Re-tokenizing here
# would be the second tokenizer that deleted every number (2026-07-27).

_HAVE_FACETS = False
try:
    from facets import assign_facet as _assign_facet   # bare import: facets.py's own convention
    _HAVE_FACETS = True
except Exception as _e:                                 # pragma: no cover
    import sys as _sys
    print("WARNING response.py: facets.assign_facet is not importable (%s) -- "
          "discourse shaping degrades to the punctuation rule it was written to "
          "replace. This is a silent-downgrade guard; see the miner-hygiene "
          "finding of 2026-07-27." % _e, file=_sys.stderr)

# cue bit -> name, mirroring config.LOGIC_CUE. Read from facets when available so
# this cannot drift from the producer (BINDINGS.md: no second scheme).
_CUE_BITS = {
    0x0001: 'CLAIM_CUE',   0x0002: 'EVIDENCE_CUE', 0x0004: 'INFERENCE',
    0x0008: 'CONTRAST',    0x0010: 'CAUSE',        0x0020: 'CONDITION',
    0x0040: 'QUANTIFIER',  0x0080: 'NEGATION',     0x0100: 'CONJUNCTION',
    0x0200: 'QUESTION',    0x0400: 'MODAL',        0x0800: 'DEFINITION_CUE',
    0x1000: 'CONCESSION',  0x2000: 'COMPARISON',   0x4000: 'TEMPORAL',
}
_UTILITY_MASK, _UTILITY_SHIFT = 0xC0, 6


def cue_read(surfaces):
    """[(surface, {cue names}, utility)] for each surface, via assign_facet.

    Empty when facets are unavailable -- the caller then keeps its old behaviour
    rather than acting on a read that isn't there."""
    if not _HAVE_FACETS or not surfaces:
        return []
    out = []
    for s in surfaces:
        try:
            _bucket, cue_mask, flags = _assign_facet(s)
        except Exception:
            continue
        names = {n for bit, n in _CUE_BITS.items() if cue_mask & bit}
        util = (flags & _UTILITY_MASK) >> _UTILITY_SHIFT     # 0 CONTENT, 1 FUNCTION
        out.append((s, names, util))
    return out


@dataclass(frozen=True)
class Shape:
    """What KIND of turn this is, read from the cue mask. Never what it is about."""
    name: str                  # definition|modal_choice|conditional|causal|comparison|question|statement
    cues: frozenset = frozenset()
    options: tuple = ()        # the alternatives in a choice ('drive', 'walk')
    compared: tuple = ()       # the two sides of a comparison
    subject: str = ''          # the multiword topic, when the turn has one

    def to_dict(self) -> dict:
        return {"shape": self.name, "cues": sorted(self.cues),
                "options": list(self.options), "compared": list(self.compared),
                "subject": self.subject}


def _content_neighbours(read, i):
    """Nearest CONTENT surface to the left and right of position i."""
    left = next((read[j][0] for j in range(i - 1, -1, -1) if read[j][2] == 0), '')
    right = next((read[j][0] for j in range(i + 1, len(read)) if read[j][2] == 0), '')
    return left, right


def read_shape(surfaces) -> Shape:
    """Classify a turn by its cue mask.

    Order matters and is not arbitrary: a turn can carry several cues at once
    ('if ... should ... or ...' carries CONDITION, MODAL and CONJUNCTION), and the
    most specific reading wins. `cognition()`'s stance ladder returns one label and
    drops the rest; this keeps every cue on the Shape so a caller can say more.

    The subject prefers a MULTIWORD content surface -- the same tiebreak
    `_wonder_on_gap` settled on after measuring that 'walk', 'drive', 'car',
    'wash' and 'feet' are all bucket=TOPIC and so buckets cannot separate the
    subject of a question from the options inside it."""
    read = cue_read(surfaces)
    if not read:
        return Shape('statement')
    cues = set()
    for _s, names, _u in read:
        cues |= names

    content = [s for s, _n, u in read if u == 0]
    multi = [c for c in content if ' ' in c]
    subject = max(multi, key=len) if multi else (content[0] if content else '')

    options, compared = (), ()
    for i, (s, names, _u) in enumerate(read):
        low = s.lower()
        if low == 'or' and 'CONJUNCTION' in names:
            # The first option follows the MODAL, it is not the nearest content word
            # to the left of 'or'. Measured on the live transcript 2026-07-26:
            # 'should I drive my car there or walk' has 'car' nearest-left, so the
            # nearest rule reported a choice between 'car' and 'walk'. In a modal
            # choice the alternatives are the actions the modal governs, so anchor
            # the left option to the first content surface AFTER the modal.
            _n, b = _content_neighbours(read, i)
            mi = next((j for j, (_s2, n2, _u2) in enumerate(read)
                       if 'MODAL' in n2 and j < i), None)
            a = ''
            if mi is not None:
                a = next((read[j][0] for j in range(mi + 1, i) if read[j][2] == 0), '')
            if not a:
                a, _r = _content_neighbours(read, i)
            if a and b:
                options = (a, b)
        if low in ('than', 'versus', 'vs') and 'COMPARISON' in names:
            # LEFTMOST content, not nearest: in 'is a hard drive faster than a car
            # wash' the nearest content left of 'than' is the comparative adjective
            # 'faster', not the thing being compared. The subject leads the clause.
            left = next((read[j][0] for j in range(i) if read[j][2] == 0), '')
            _n, right = _content_neighbours(read, i)
            if left and right:
                compared = (left, right)

    # Which surface carried QUESTION -- the mask says a question was asked, not
    # what kind. DICTIONARY GAP, filed not patched: 'why' belongs in
    # config.LOGIC_SEED_LISTS['CAUSE']. Adding it there would fix this at the
    # source, but assign_facet computes at runtime while the browser reads a
    # prebuilt facets.bin, so editing the seed list desynchronises the two until a
    # rebuild. Read the surface here; move it to the lexicon at the next build.
    qwords = {s.lower() for s, names, _u in read if 'QUESTION' in names}

    # EMBEDDED WH IS NOT A QUESTION (live 2026-07-31): the TEACH "A car wash is a
    # place where you take your car to have it washed" carries a QUESTION cue on
    # 'where' -- a relative clause -- plus DEFINITION_CUE on 'is', and was answered
    # with "I have not been told what 'car wash' means. What is it?": Elo re-asking
    # the question WHILE BEING TAUGHT the answer. The structural tell: an assertion
    # verb BEFORE the first question-cued surface means the wh serves the assertion.
    # "What is a car wash" -> wh first -> question. "A car wash is a place where..."
    # -> 'is' first -> statement.
    _q_i = next((i for i, (_s, n, _u) in enumerate(read) if 'QUESTION' in n), None)
    _a_i = next((i for i, (sf, _n, _u) in enumerate(read)
                 if sf.lower() in ('is', 'are', 'means')), None)
    is_teach = (_q_i is not None and _a_i is not None and _a_i < _q_i)

    if options and 'MODAL' in cues:
        name = 'modal_choice'
    elif compared:
        name = 'comparison'
    elif not is_teach and ('why' in qwords
                           or ('CAUSE' in cues and 'QUESTION' in cues)):
        name = 'causal'
    elif 'CONDITION' in cues:
        name = 'conditional'
    elif not is_teach and 'DEFINITION_CUE' in cues and 'QUESTION' in cues:
        name = 'definition'
    elif not is_teach and 'QUESTION' in cues:
        name = 'question'
    else:
        name = 'statement'
    return Shape(name, frozenset(cues), options, compared, subject)


# Shape-specific forms for a turn recall could not answer. The generic
# "I have nothing stored about {concept} yet" is TEMPLATES['unknown']; these say
# what was actually asked, so the gap is legible instead of blank.
SHAPE_TEMPLATES = {
    'modal_choice': ("That is a choice between {a} and {b}"
                     "{about} -- and I have nothing stored that tells me how you weigh it. "
                     "What matters here?"),
    'comparison':   ("That compares {a} against {b}, and I have nothing stored that "
                     "measures them against each other."),
    'causal':       ("That asks why{about}. I have nothing stored about the cause."),
    'conditional':  ("That sets a condition I have nothing stored{about}. "
                     "Tell me what follows from it and I will keep it."),
    'definition':   ("I have not been told what{about} means. What is it?"),
    'question':     ("I have nothing stored about{about} yet."),
}


def shape_unknown_reply(shape: Shape, fallback: str) -> str:
    """The 'I could not answer' reply, in the shape of the question asked.

    Falls back verbatim when the shape carries nothing to say -- a reply that
    names no subject and no options is not an improvement on the generic one."""
    tpl = SHAPE_TEMPLATES.get(shape.name)
    if not tpl:
        return fallback
    about = f" about '{shape.subject}'" if shape.subject else ""
    if shape.name == 'definition':
        about = f" '{shape.subject}'" if shape.subject else ""
    if shape.name == 'modal_choice':
        if not shape.options:
            return fallback
        return tpl.format(a=shape.options[0], b=shape.options[1], about=about)
    if shape.name == 'comparison':
        if not shape.compared:
            return fallback
        return tpl.format(a=shape.compared[0], b=shape.compared[1])
    if not shape.subject:
        return fallback
    return tpl.format(about=about)


# The turn recall COULD answer, said in the shape of the question. The seed is real
# and cited either way; what changes is whether the reply uses it or merely reads it
# aloud. Measured 2026-07-26: asked "should I drive or walk?" with 'You are 100 feet
# from the car wash.' recalled, the reply was 'From what you have told me: "You are
# 100 feet from the car wash."' -- the right fact, quoted at someone who just asked a
# question it does not literally answer.
#
# These never decide FOR the user. A modal choice with a distance fact stored still
# does not tell Elo whether 100 feet is far; it can state what it holds, name the
# alternatives, and say plainly which part it has nothing for.
GROUNDED_TEMPLATES = {
    'modal_choice': 'You told me: "{fact}" Between {a} and {b} -- nothing I have '
                    'stored says how you weigh that. What matters to you here?',
    # INTENT GUARD (live 2026-07-31): the user answered "I want to get the car
    # washed", Elo re-asked the choice and claimed "nothing I have stored says how
    # you weigh that" -- a false statement made while HOLDING the answer. When a
    # recalled seed carries intent, cite it and name only the genuinely open part.
    # Never fake the inference from intent to option (car wash washes cars => the
    # car goes => drive is REASONING, not templating).
    'modal_choice_intent':      'You told me: "{fact}" And you told me: "{intent}" '
                                'Neither settles {a} versus {b} on its own -- which '
                                'way do you lean?',
    'modal_choice_intent_same': 'You told me: "{fact}" That says what you want -- '
                                'not whether {a} or {b}. Which way?',
    # MENTION IS NOT DEFINITION (live 2026-07-31): asked "do you know what a car
    # wash is?", recall served the intent seed "I want to wash the car" -- the only
    # seed containing the words -- and the fallback quoted it as if it answered.
    # A seed that MENTIONS the subject does not DEFINE it. When the top seed is not
    # definitional, say exactly that split: what is held, what is missing.
    'definition_mention':       'You told me: "{fact}" -- that mentions {subject}, '
                                'but I have not been told what {subject} is. '
                                'What is a {subject}?',
    'comparison':   'You told me: "{fact}" That is one side of it; I have nothing '
                    'that measures {a} against {b}.',
    'causal':       'You told me: "{fact}" That is what I hold -- nothing stored '
                    'says why.',
    'conditional':  'You told me: "{fact}" What follows from that condition is not '
                    'something I have been told.',
}


def shape_grounded_reply(shape: Shape, fact: str, fallback: str,
                         intent: str = None) -> str:
    """Compose the recalled fact into the shape of the question.

    Returns `fallback` unchanged for shapes with no specific form, and for shapes
    whose slots are empty -- a reply naming neither options nor compared terms is
    the quoting fallback with extra words."""
    tpl = GROUNDED_TEMPLATES.get(shape.name)
    if not fact:
        return fallback
    if not tpl and shape.name != 'definition':   # definition composes without a base tpl
        return fallback
    fact = fact.strip()
    if shape.name == 'modal_choice':
        if len(shape.options) < 2:
            return fallback
        a, b = shape.options[0], shape.options[1]
        if intent:
            intent = intent.strip()
            if intent == fact:
                return GROUNDED_TEMPLATES['modal_choice_intent_same'].format(
                    fact=fact, a=a, b=b)
            return GROUNDED_TEMPLATES['modal_choice_intent'].format(
                fact=fact, intent=intent, a=a, b=b)
        return tpl.format(fact=fact, a=a, b=b)
    if shape.name == 'comparison':
        if len(shape.compared) < 2:
            return fallback
        return tpl.format(fact=fact, a=shape.compared[0], b=shape.compared[1])
    if shape.name == 'definition':
        if not shape.subject:
            return fallback
        # A definitional seed ('car wash is/are/means ...') answers a definition
        # question -- the quoting fallback serves it verbatim, which is right.
        # A seed that merely mentions the subject does not.
        if re.search(rf"\b{re.escape(shape.subject)}\s+(?:is|are|means)\b",
                     fact, re.I):
            return fallback
        return GROUNDED_TEMPLATES['definition_mention'].format(
            fact=fact, subject=shape.subject)
    return tpl.format(fact=fact)
