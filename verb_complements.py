"""
verb_complements.py -- canonical verb subcategorization (the "instruction set").

A noun/verb homograph after "to" is resolved by the GOVERNING verb's complement
pattern: is "to" an INFINITIVE marker (the word after it is a VERB) or a
PREPOSITION (a NOUN)?

    "the deadline seemed to change"   seem licenses TO_INFINITIVE -> change=VERB
    "the MCP server maps to code"     map  licenses TO_NOUN       -> code=NOUN

This table is the SYSTEM-1 source of truth. It is surfaced into every dictionary
build as the per-surface `complement` column of the meta DB (meta_fields.py), so
the data ships WITH the dictionary ("dictionary as instruction set"). The
extraction pipeline (05-ExtractionPipeline) is a consumer: it reads the column
from a bound dictionary's meta.db and falls back to its own bundled copy when no
dictionary is bound. Keep this table and that bundled copy in sync; this is the
authoritative one. No model -- a lexicon + light lemmatizer.
"""
from __future__ import annotations

# patterns: 'to_infinitive' (word after "to" is a verb) | 'to_noun' (a noun).
VERB_COMPLEMENTS: dict[str, frozenset] = {
    # take a to-INFINITIVE complement  ->  the word after "to" is a VERB
    "seem": frozenset({"to_infinitive"}), "appear": frozenset({"to_infinitive"}),
    "try": frozenset({"to_infinitive"}), "attempt": frozenset({"to_infinitive"}),
    "want": frozenset({"to_infinitive"}), "wish": frozenset({"to_infinitive"}),
    "intend": frozenset({"to_infinitive"}), "plan": frozenset({"to_infinitive"}),
    "expect": frozenset({"to_infinitive"}), "hope": frozenset({"to_infinitive"}),
    "decide": frozenset({"to_infinitive"}), "choose": frozenset({"to_infinitive"}),
    "begin": frozenset({"to_infinitive"}), "start": frozenset({"to_infinitive"}),
    "continue": frozenset({"to_infinitive"}), "need": frozenset({"to_infinitive"}),
    "manage": frozenset({"to_infinitive"}), "refuse": frozenset({"to_infinitive"}),
    "agree": frozenset({"to_infinitive"}), "offer": frozenset({"to_infinitive"}),
    "fail": frozenset({"to_infinitive"}), "tend": frozenset({"to_infinitive"}),
    "prepare": frozenset({"to_infinitive"}), "promise": frozenset({"to_infinitive"}),
    "learn": frozenset({"to_infinitive"}), "like": frozenset({"to_infinitive"}),
    "love": frozenset({"to_infinitive"}), "prove": frozenset({"to_infinitive"}),
    # take a to-NOUN (prepositional) object  ->  the word after "to" is a NOUN
    "map": frozenset({"to_noun"}), "connect": frozenset({"to_noun"}),
    "relate": frozenset({"to_noun"}), "lead": frozenset({"to_noun"}),
    "point": frozenset({"to_noun"}), "refer": frozenset({"to_noun"}),
    "belong": frozenset({"to_noun"}), "listen": frozenset({"to_noun"}),
    "talk": frozenset({"to_noun"}), "respond": frozenset({"to_noun"}),
    "switch": frozenset({"to_noun"}), "adapt": frozenset({"to_noun"}),
    "contribute": frozenset({"to_noun"}), "react": frozenset({"to_noun"}),
    "amount": frozenset({"to_noun"}),
    # license BOTH  ->  consumers fall back to the candidate word's own POS
    "go": frozenset({"to_infinitive", "to_noun"}),
    "come": frozenset({"to_infinitive", "to_noun"}),
    "get": frozenset({"to_infinitive", "to_noun"}),
    "return": frozenset({"to_infinitive", "to_noun"}),
}


def verb_lemma(token: str) -> str:
    """Light inflection stripping to match VERB_COMPLEMENTS keys. No model: build
    candidate bases and return the first that is a known verb. Handles regular
    (seemed->seem, maps->map, going->go), e-drop (decided->decide), y->ied
    (tried/tries->try)."""
    w = (token or "").lower().strip(".,!?;:'\"()[]")
    if w in VERB_COMPLEMENTS:
        return w
    cands = []
    if w.endswith(("ied", "ies")):
        cands.append(w[:-3] + "y")
    for suf in ("ing", "ed", "es", "s", "d"):
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            base = w[: -len(suf)]
            cands.append(base)
            cands.append(base + "e")
            if len(base) >= 2 and base[-1] == base[-2] and base[-1] not in "aeiou":
                cands.append(base[:-1])   # de-double: mapped->map, planned->plan
    for c in cands:
        if c in VERB_COMPLEMENTS:
            return c
    return w


def complement_of(surface: str) -> list | None:
    """The complement pattern(s) for a (possibly inflected) verb surface, sorted;
    None for any surface whose lemma is not a known verb. This is what the meta
    builder writes into the `complement` column (keyed by surface)."""
    comp = VERB_COMPLEMENTS.get(verb_lemma(surface))
    return sorted(comp) if comp else None


if __name__ == "__main__":
    for s in ["seem", "seemed", "maps", "map", "tried", "decided", "going",
              "dog", "the", "machine"]:
        print(f"  {s:<10} -> {complement_of(s)}")
