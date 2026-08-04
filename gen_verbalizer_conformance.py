"""
gen_verbalizer_conformance.py -- generate the verbalizer-ops conformance file.

`(args, expected)` cases produced BY the reference ops (never hand-authored), so
the file is the contract AND the graduation certificate: the browser port (JS/Rust)
must reproduce every `expected`. Same pattern as
discourse/conversation_conformance.json.

    python gen_verbalizer_conformance.py         # writes verbalizer_conformance.json

Rule (invariant #7): a case marked `requires:"facets"` must be generated WITH the
facet channel present, or the certificate would encode a silently-degraded output.
The generator refuses rather than emit one.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verbalizer_ops import label, summarize, verbalize      # noqa: E402
from response import cue_read                                # noqa: E402

CONFORMANCE_PATH = Path(__file__).resolve().parent / "verbalizer_conformance.json"
_FACETS = bool(cue_read(["word"]))


def run_case(case) -> dict:
    """Dispatch a case's args to its op. Shared by the generator and the runner."""
    op, a = case["op"], case["args"]
    if op == "label":
        return label(a["items"], a.get("context"))
    if op == "summarize":
        return summarize(a["items"], a["budget"])
    if op == "verbalize":
        return verbalize(a["input"])
    raise ValueError(f"unknown op {op!r}")


# a licensed inference chain (choice resolver output) for the B3 chain cases
_CHAIN = [
    {"src": "washing car matters", "rel": "CO_PRESENCE", "dst": "car present",
     "rule": "co_presence", "ceiling": 0.65, "stance": "told",
     "text": "washing the car matters"},
    {"src": "driving", "rel": "ENABLES", "dst": "car present",
     "rule": "enables", "ceiling": 0.65, "stance": "told",
     "text": "Driving takes the car with you"},
    {"src": "walking", "rel": "PREVENTS", "dst": "car present",
     "rule": "blocks", "ceiling": 0.75, "stance": "told",
     "text": "Walking leaves it behind"},
]


def _cv(**over):
    cv = {"kind": "recommend", "option": "drive",
          "verdicts": {"drive": "SATISFIES", "walk": "DEFEATS"},
          "requirement": "car present", "requirement_source": "axiom",
          "chain": _CHAIN, "confidence": 0.65, "stance": "told", "ask": None}
    cv.update(over)
    return cv


# --- the cases (>=10 per op, incl. degradation) ----------------------------
CASES = [
    # ---- label ----
    {"op": "label", "args": {"items": [{"id": "i1", "text": "x"}],
                             "context": {"surfaces": ["car wash", "drive", "walk"]}}},
    {"op": "label", "args": {"items": [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}],
                             "context": {"surfaces": ["system"]}}},
    {"op": "label", "args": {"items": [{"id": "i", "text": "x"}],
                             "context": {"surfaces": ["the quick brown fox jumps"]}}},
    {"op": "label", "args": {"items": [{"id": "i", "text": "x"}],
                             "context": {"surfaces": ["reactor"]}}},
    {"op": "label", "args": {"items": [{"id": "i", "text": "x"}],
                             "context": {"surfaces": ["engine", "engine", "turbo boost"]}}},
    {"op": "label", "args": {"items": [{"id": "i", "text": "x"}],
                             "context": {"surfaces": ["alpha", "beta"]}}},
    {"op": "label", "args": {"items": [], "context": {"surfaces": []}}},          # empty (degrade)
    {"op": "label", "args": {"items": [{"id": "i", "text": "quantum reactor core"}]}},  # no surfaces (degrade)
    {"op": "label", "requires": "facets",
     "args": {"items": [{"id": "i", "text": "x"}],
              "context": {"surfaces": ["the", "the", "the", "system"]}}},          # facet drops 'the'
    {"op": "label", "requires": "facets",
     "args": {"items": [{"id": "i", "text": "x"}],
              "context": {"surfaces": ["and", "reactor", "of"]}}},                 # facet drops glue

    # ---- summarize ----
    {"op": "summarize", "args": {"items": [
        {"id": "s1", "kind": "filler", "text": "well anyway"},
        {"id": "s2", "kind": "fact", "text": "the server has 128 GB of ram"},
        {"id": "s3", "kind": "intent", "text": "i want to upgrade it"}], "budget": 2}},
    {"op": "summarize", "args": {"items": [
        {"id": "s1", "kind": "filler", "text": "well anyway"},
        {"id": "s2", "kind": "fact", "text": "the server has 128 GB of ram"},
        {"id": "s3", "kind": "intent", "text": "i want to upgrade it"}], "budget": 1}},
    {"op": "summarize", "args": {"items": [
        {"id": "s2", "kind": "fact", "text": "the server has 128 GB of ram"}], "budget": 1}},
    {"op": "summarize", "args": {"items": [
        {"id": "a", "kind": "fact", "text": "one"},
        {"id": "b", "kind": "fact", "text": "two"}], "budget": 5}},                # budget > len
    {"op": "summarize", "args": {"items": [
        {"id": "a", "kind": "fact", "text": "one"}], "budget": 0}},                # budget 0 (degrade)
    {"op": "summarize", "args": {"items": [], "budget": 3}},                       # empty (degrade)
    {"op": "summarize", "args": {"items": [
        {"id": "a", "text": "first note"}, {"id": "b", "text": "second note"}], "budget": 2}},  # no kind
    {"op": "summarize", "args": {"items": [
        {"id": "a", "kind": "fact", "text": "lowercase start no period"}], "budget": 1}},
    {"op": "summarize", "args": {"items": [
        {"id": "a", "kind": "intent", "text": "ship it"},
        {"id": "b", "kind": "fact", "text": "tests pass"}], "budget": 1}},         # fact outranks intent
    {"op": "summarize", "args": {"items": [
        {"id": "a", "kind": "fact", "text": "price is 42 dollars"}], "budget": 1}},  # number

    # ---- verbalize ----
    {"op": "verbalize", "args": {"input": {
        "shape": {"shape": "definition", "subject": "car wash"}, "seeds": []}}},
    {"op": "verbalize", "args": {"input": {
        "shape": {"shape": "modal_choice", "options": ["drive", "walk"], "subject": "car wash"},
        "seeds": [{"id": "s1", "kind": "fact", "text": "You are 100 feet from the car wash", "stance": "told"}]}}},
    {"op": "verbalize", "args": {"input": {
        "shape": {"shape": "definition", "subject": "car wash"},
        "seeds": [{"id": "s1", "text": "i want to wash the car", "stance": "told"}]}}},
    {"op": "verbalize", "args": {"input": {
        "shape": {"shape": "definition", "subject": "car wash"},
        "seeds": [{"id": "s1", "text": "a car wash is a place where you clean your car", "stance": "told"}]}}},
    {"op": "verbalize", "args": {"input": {
        "query": "whats your name?", "seeds": [{"id": "s1", "text": "Your name is Elo"}]}}},
    {"op": "verbalize", "args": {"input": {"query": "hello", "seeds": []}}},
    {"op": "verbalize", "args": {"input": {
        "shape": {"shape": "statement"},
        "seeds": [{"id": "r1", "text": "the deploy contributes_to the outage", "stance": "inferred"}]}}},
    {"op": "verbalize", "args": {"input": {
        "seeds": [{"id": "s1", "text": "the sky is blue", "stance": "told"}]}}},   # no shape
    {"op": "verbalize", "args": {"input": {"seeds": []}}},                         # empty (degrade)
    {"op": "verbalize", "args": {"input": {
        "shape": {"shape": "comparison", "compared": ["hard drive", "car wash"], "subject": "speed"},
        "seeds": [{"id": "s1", "text": "a hard drive is fast", "stance": "told"}]}}},
    {"op": "verbalize", "args": {"input": {
        "shape": {"shape": "statement"}, "answered": {"question": "your name", "subject": "name"},
        "seeds": [{"id": "s1", "text": "you said hi", "stance": "told"}]}}},       # answered weave
    {"op": "verbalize", "args": {"input": {
        "shape": {"shape": "statement"},
        "seeds": [{"id": "s1", "text": "it might rain tomorrow", "stance": "speculation"}]}}},  # stance
    # --- B3 chain verbalization: one per kind + perception + taught + fall-through
    {"op": "verbalize", "args": {"input": {"chain_verdict": _cv()}}},                 # recommend
    {"op": "verbalize", "args": {"input": {"chain_verdict": _cv(
        kind="recommend_gap", verdicts={"drive": "SATISFIES", "walk": "UNKNOWN"},
        ask="Does walk affect car present?")}}},                                      # recommend_gap
    {"op": "verbalize", "args": {"input": {"chain_verdict": _cv(
        kind="tie", option=None, verdicts={"drive": "SATISFIES", "cycle": "SATISFIES"})}}},  # tie
    {"op": "verbalize", "args": {"input": {"chain_verdict": _cv(
        kind="neither", option=None, verdicts={"walk": "DEFEATS", "swim": "DEFEATS"})}}},     # neither
    {"op": "verbalize", "args": {"input": {"chain_verdict": _cv(stance="perception")}}},      # John rule
    {"op": "verbalize", "args": {"input": {"chain_verdict": _cv(requirement_source="taught")}}},  # taught vs axiom
    {"op": "verbalize", "args": {"input": {"chain_verdict": _cv(                        # goal_core NEVER read back
        chain=[dict(_CHAIN[0], text="the car to be clean so i want to have the car washed"),
               _CHAIN[1], _CHAIN[2]])}}},
    {"op": "verbalize", "args": {"input": {"chain_verdict": _cv(                        # full surface IS voiced
        goal_surface="washing the car matters")}}},
    {"op": "verbalize", "args": {"input": {                                            # unknown -> fall-through
        "chain_verdict": {"kind": "unknown", "chain": _CHAIN}, "seeds": []}}},
]


def main() -> int:
    for c in CASES:
        if c.get("requires") == "facets" and not _FACETS:
            print("REFUSING: a facets-required case cannot be generated without the "
                  "facet channel -- the certificate would encode a degraded output "
                  "(invariant #7). Run where facets.assign_facet imports.",
                  file=sys.stderr)
            return 1
    records = []
    for c in CASES:
        rec = {"op": c["op"], "args": c["args"], "expected": run_case(c)}
        if c.get("requires"):
            rec["requires"] = c["requires"]
        records.append(rec)
    payload = {"version": 1, "facets_present": _FACETS, "cases": records}
    CONFORMANCE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    per = {}
    for r in records:
        per[r["op"]] = per.get(r["op"], 0) + 1
    print(f"wrote {len(records)} cases to {CONFORMANCE_PATH.name}: "
          + ", ".join(f"{k} {v}" for k, v in per.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
