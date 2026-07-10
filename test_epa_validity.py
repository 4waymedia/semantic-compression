"""
test_epa_validity.py -- the EPA nearest-neighbour validity gate.

Locks two things:
  1. the PURE arithmetic (confidence / is_reliable / guard) -- no I/O, always runs;
  2. the CALIBRATED behaviour on the real substrate -- affect words answer,
     denotative words abstain. Skipped cleanly if the substrate is absent.

The abstention is the feature: an empty result means "EPA cannot distinguish this
word's neighbours", which is a correct answer. Emitting the raw top-k instead
produces noise (`doctor` -> 'noted', 'good cuz', 'dining hall').

    python test_epa_validity.py        (from semantic_compression/)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

from epa_validity import (  # noqa: E402
    EPA_NN_DEFAULT_K, EPA_NN_MIN_CONFIDENCE, EPA_NN_RADIUS,
    EPAGate, confidence, guard, is_reliable,
)

_SUBSTRATE = Path("../Memory/data/epa_substrate.lmdb")

# calibrated 2026-07-09: affect words n_r <= 19, denotative n_r >= 56
AFFECT_WORDS = ["happy", "murder", "joy", "terrified"]
DENOTATIVE_WORDS = ["rabbit", "car", "doctor", "big"]


# --- pure arithmetic ---------------------------------------------------------

def test_confidence_is_share_of_candidate_set():
    # n_r <= k -> we return every candidate; nothing is arbitrarily chosen
    assert confidence(0, k=5) == 1.0
    assert confidence(3, k=5) == 1.0
    assert confidence(5, k=5) == 1.0
    # n_r > k -> we return k of n_r
    assert abs(confidence(10, k=5) - 0.5) < 1e-9
    assert abs(confidence(100, k=5) - 0.05) < 1e-9
    assert confidence(92, k=5) < EPA_NN_MIN_CONFIDENCE      # 'doctor'
    assert confidence(19, k=5) >= EPA_NN_MIN_CONFIDENCE     # 'terrified'
    assert confidence(1, k=0) == 0.0                        # degenerate k


def test_is_reliable_matches_the_separation_gap():
    # measured gap: affect max n_r = 19, denotative min n_r = 56
    assert is_reliable(19), "terrified (n_r=19) must be answerable"
    assert not is_reliable(56), "rabbit (n_r=56) must abstain"


def test_guard_abstains_by_returning_empty():
    raw = ["noted", "good cuz", "dining hall", "means", "done yeah"]
    kept, c, why = guard(raw, n_r=92)          # doctor
    assert kept == [], "must not emit noise from a dense region"
    assert c < EPA_NN_MIN_CONFIDENCE
    assert "abstain" in why and "92" in why    # reason names the density

    kept, c, why = guard(["joy", "cheerful"], n_r=1)   # happy
    assert kept == ["joy", "cheerful"] and c == 1.0 and why == "ok"


def test_guard_truncates_to_k():
    kept, _, _ = guard(list("abcdefgh"), n_r=0, k=3)
    assert kept == ["a", "b", "c"]


# --- calibrated behaviour on the real substrate ------------------------------

def test_substrate_gate():
    if not _SUBSTRATE.exists():
        print(f"  [skip] substrate not found at {_SUBSTRATE}")
        return
    gate = EPAGate.from_lmdb(_SUBSTRATE)
    assert gate.vectors.shape[1] == 3, "EPA must be 3-d"

    for w in AFFECT_WORDS:
        nb, c, why = gate.neighbors(w)
        assert nb, f"{w!r} is affect-laden and must ANSWER (conf={c:.3f}, {why})"

    for w in DENOTATIVE_WORDS:
        nb, c, why = gate.neighbors(w)
        assert nb == [], f"{w!r} is denotative and must ABSTAIN (got {nb})"
        assert c < EPA_NN_MIN_CONFIDENCE

    # never guess for an unrated surface
    nb, c, why = gate.neighbors("zzz_not_a_real_surface_zzz")
    assert nb == [] and c == 0.0 and "no EPA rating" in why


def main() -> None:
    tests = [test_confidence_is_share_of_candidate_set,
             test_is_reliable_matches_the_separation_gap,
             test_guard_abstains_by_returning_empty,
             test_guard_truncates_to_k,
             test_substrate_gate]
    for t in tests:
        t()
        print(f"[OK] {t.__name__}")
    print(f"\n=== test_epa_validity: {len(tests)}/{len(tests)} PASSED "
          f"(r={EPA_NN_RADIUS}, k={EPA_NN_DEFAULT_K}, min_conf={EPA_NN_MIN_CONFIDENCE}) ===")


if __name__ == "__main__":
    main()
