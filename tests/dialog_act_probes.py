"""
dialog_act_probes.py -- labeled probe set + accuracy measurement for the
deterministic dialog-act classifier (response.dialog_reply).

Each probe is (utterance, expected_act). expected_act == None means the turn is
NOT a dialog act and must fall through to fact recall (dialog_reply returns None).
This is the roadmap's "labeled dialog-act probe set" gate (target >= 0.90) and the
reproducible headline number for the conversation-mechanics paper.

Run:  python tests/dialog_act_probes.py    (from the semantic_compression dir)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # allow direct run
from response import dialog_reply  # noqa: E402

# (utterance, expected_act). None => must fall through to recall (returns None).
PROBES = [
    # greet
    ("hi", "greet"),
    ("hello", "greet"),
    ("hey there", "greet"),
    ("good morning", "greet"),
    # introduce (speaker self-intro)
    ("my name is Paul", "introduce"),
    ("hi, my name is Sarah", "introduce"),
    ("your name is Elo. my name is Paul", "introduce"),   # mutual intro
    # acknowledge_name (setting Elo's name)
    ("your name is Elo", "acknowledge_name"),
    ("your name is Atlas", "acknowledge_name"),
    # identity / capability
    ("who are you?", "identity"),
    ("what are you?", "identity"),
    ("what can you do?", "identity"),
    ("what do you do?", "identity"),
    # state check
    ("how are you?", "state_check"),
    ("how are you today?", "state_check"),
    ("how's it going?", "state_check"),
    ("how are things?", "state_check"),
    # opinion
    ("do you like the weather?", "opinion"),
    ("do you enjoy music?", "opinion"),
    ("what do you think?", "opinion"),
    ("how do you feel about that?", "opinion"),
    # location
    ("where are you?", "location"),
    ("where do you live?", "location"),
    ("where do you run?", "location"),
    # origin
    ("who made you?", "origin"),
    ("who created you?", "origin"),
    ("where did you come from?", "origin"),
    # values
    ("what do you value?", "values"),
    ("what do you believe?", "values"),
    ("what are your principles?", "values"),
    # accept a conversation
    ("let's talk", "accept_meta"),
    ("I want to have a conversation", "accept_meta"),
    ("can we chat?", "accept_meta"),
    # thanks / farewell
    ("thanks", "thanks"),
    ("thank you", "thanks"),
    ("bye", "farewell"),
    ("goodbye", "farewell"),
    # plain statements -> acknowledge
    ("the sky is blue", "acknowledge"),
    ("my deadline is Friday", "acknowledge"),
    # fact questions -> NOT acts; must fall to recall (None)
    ("what is your name?", None),
    ("what is my name?", None),
    ("what is my deadline?", None),
    # a bare doing-question must not trip identity
    ("what are you doing?", None),
]


def classify(utterance, self_model=None):
    d = dialog_reply(utterance, self_model=self_model)
    return d[1] if d is not None else None


def run(self_model=None, verbose=True):
    correct = 0
    misses = []
    for utt, expected in PROBES:
        got = classify(utterance=utt, self_model=self_model)
        ok = (got == expected)
        correct += ok
        if not ok:
            misses.append((utt, expected, got))
        if verbose:
            flag = "ok " if ok else "MISS"
            print(f"  [{flag}] {utt!r:<46} expected={expected!s:<16} got={got!s}")
    n = len(PROBES)
    print(f"\n  dialog-act accuracy: {correct}/{n} = {correct / n:.3f}")
    if misses:
        print("  misses:")
        for utt, exp, got in misses:
            print(f"    {utt!r} expected {exp} got {got}")
    return correct, n, misses


if __name__ == "__main__":
    run()
