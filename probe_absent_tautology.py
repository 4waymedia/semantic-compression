"""Is 100%-where-expected a MEASUREMENT, or a rule that cannot say 'unknown'?

    python semantic_compression/probe_absent_tautology.py elo-v5

WHY THIS EXISTS (elo-sdm, 2026-09-16, and it is their design).

The census reports `temporal` at 6.4% pooled and **100.0% where expected**. This lane
published the 100% as evidence the channel is complete over the class it applies to.
elo-sdm's objection is the one that matters:

    "A claim that holds under both behaviours is not a claim. 100%-where-expected is
     consistent with two different worlds -- the channel measured every qualifying
     record, or the channel CANNOT EXPRESS ABSENCE over that class and a default is
     being counted as a measurement. The observation cannot separate them."

Their test: force the correct answer to be UNKNOWN and see whether UNKNOWN comes out.
If it does, 100% is a measurement. If a value still comes out, 100% is a tautology --
the same shape as `wordclass.OTHER == phrase count`.

WHAT THIS RUNS, in three parts, cheapest first:

  PART A  CONTRACT   -- can the field even represent absence? (from the .names.json)
  PART B  MUTATION   -- drive the builder's OWN decision functions with evidence
                        removed, and see what they return. This is the discriminating
                        test; A and C only frame it.
  PART C  EMITTED    -- scan the published channel for a single record that actually
                        carries the absent value over a qualifying record.

Part B imports the real functions from `vfacet_builder` rather than restating the rule.
A probe that reimplements the thing it is probing proves nothing about the thing.

Generalises to the other two exactly-100% cells by `--field`: `temporal`, `direction`,
and `facets` (whose 0xFF sentinel is declared and has never been emitted).
"""
from __future__ import annotations

import argparse
import json
import random
import string
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SC = ROOT / "semantic_compression"
sys.path.insert(0, str(SC))
BUILDS = SC / "db" / "builds"
DIST = ROOT / "dist" / "dictionary"

# Evidence-free surfaces: no English aspectual suffix, no lexicon membership, nothing a
# suffix heuristic or a word list can key on. If a rule returns a VALUE for these, it is
# not reading evidence -- there is none to read.
_NO_EVIDENCE = [
    "zorb", "quilk", "frandle", "blorp", "vexil", "mombrak", "tuvey", "skrelt",
    "yannic", "drupe", "glimf", "warthok", "penlo", "crivit", "ombalt", "nurk",
]


def _rand_words(n: int, rng: random.Random) -> list[str]:
    out = []
    for _ in range(n):
        k = rng.randint(4, 8)
        w = "".join(rng.choice(string.ascii_lowercase) for _ in range(k))
        if not any(w.endswith(s) for s in ("ing", "ed", "es", "s")):
            out.append(w)
    return out


def part_a(build: str, field: str) -> dict:
    print("PART A -- CONTRACT: can the field represent absence at all?")
    names = {"temporal": "vfacets.names.json", "direction": "vfacets.names.json",
             "facets": "facets.names.json"}[field]
    for cand in (DIST / f"{build}r3" / names, DIST / build / names,
                 BUILDS / build / names):
        if cand.is_file():
            doc = json.loads(cand.read_text(encoding="utf-8"))
            break
    else:
        print(f"  cannot find {names} for {build} -- skipping Part A\n")
        return {}
    key = {"temporal": "temporal_names", "direction": "direction_names",
           "facets": "bucket_names"}[field]
    vals = doc.get(key) or doc.get(key.replace("_names", "")) or {}
    absent_codes = [k for k, v in vals.items() if str(v).upper() in ("UNKNOWN", "ABSENT")]
    print(f"  {names} -> {key}: {len(vals)} value(s)")
    print(f"  absent/unknown code(s): {absent_codes or 'NONE DECLARED'}")
    if field == "facets":
        print(f"  bucket_absent sentinel:  {doc.get('bucket_absent')}")
    print("  => the field CAN express absence.\n" if absent_codes or doc.get("bucket_absent")
          else "  => the field CANNOT express absence. 100% is a tautology by contract.\n")
    return {"absent_codes": absent_codes}


def part_b(field: str, n_random: int, seed: int) -> dict:
    print("PART B -- MUTATION: drive the builder's own functions with the evidence removed.")
    if field != "temporal":
        print(f"  not yet wired for {field!r}; temporal is the one sdm specified.\n")
        return {}
    try:
        from vfacet_builder import (TEMPORAL, _aspect_of_inflection,   # noqa: PLC0415
                                    _classify_temporal)
    except Exception as e:                                   # noqa: BLE001
        print(f"  cannot import vfacet_builder: {type(e).__name__}: {e}\n")
        return {}
    inv = {v: k for k, v in TEMPORAL.items()}
    rng = random.Random(seed)
    probes = _NO_EVIDENCE + _rand_words(n_random, rng)

    suffix_unknown = sum(1 for w in probes
                         if _classify_temporal(w) == TEMPORAL["UNKNOWN"])
    aspect_out = Counter(inv.get(_aspect_of_inflection(w), "?") for w in probes)

    print(f"  probes with NO temporal evidence: {len(probes)}")
    print(f"  _classify_temporal -> UNKNOWN:    {suffix_unknown}/{len(probes)}"
          f"   (the suffix rule CAN say unknown)")
    print(f"  _aspect_of_inflection ->          {dict(aspect_out)}")
    never_unknown = aspect_out.get("UNKNOWN", 0) == 0
    print()
    if never_unknown:
        print("  => `_aspect_of_inflection` is TOTAL: it returns a value for every input,")
        print("     including inputs carrying no evidence whatsoever. Its final line is")
        print("     `return TEMPORAL['PROCESS']`, documented as 'the unmarked base form")
        print("     keeps PROCESS'. A VERB therefore CANNOT come out UNKNOWN.\n")
    else:
        print("  => the aspect rule can return UNKNOWN; 100% is not a tautology here.\n")
    return {"total": never_unknown, "distribution": dict(aspect_out)}


def part_c(build: str, field: str) -> dict:
    print("PART C -- EMITTED: does any published record carry the absent value?")
    census = BUILDS / build / "coverage_census.json"
    if not census.is_file():
        print(f"  no coverage_census.json in {BUILDS / build} -- skipping\n")
        return {}
    ch = (json.loads(census.read_text(encoding="utf-8")).get("channels") or {}).get(field)
    if not ch:
        print(f"  census has no channel {field!r}\n")
        return {}
    dist = ch.get("value_distribution") or {}
    print(f"  qualifying_classes   {ch.get('qualifying_classes')}")
    print(f"  ambivalent_excluded  {ch.get('ambivalent_excluded')}")
    print(f"  where expected       {ch.get('coverage_where_expected_pct')}%")
    print(f"  value_distribution   {dist}")
    unknown = sum(v for k, v in dist.items() if k.upper() in ("UNKNOWN", "ABSENT"))
    total = sum(dist.values())
    print(f"  records carrying UNKNOWN/ABSENT: {unknown} of {total}\n")
    return {"unknown": unknown, "total": total}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("build", nargs="?", default="elo-v5")
    ap.add_argument("--field", default="temporal",
                    choices=("temporal", "direction", "facets"))
    ap.add_argument("--random", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20260921)
    a = ap.parse_args(argv)

    print(f"\nprobe_absent_tautology  build={a.build}  field={a.field}\n" + "-" * 72)
    ca = part_a(a.build, a.field)
    cb = part_b(a.field, a.random, a.seed)
    cc = part_c(a.build, a.field)

    print("-" * 72)
    can_express = bool(ca.get("absent_codes"))
    rule_total = cb.get("total")
    emitted = cc.get("unknown")

    if rule_total and can_express and emitted == 0:
        print("VERDICT: TAUTOLOGY.")
        print("  The field can express absence, the rule never produces it, and no record")
        print("  carries it. 100%-where-expected measures the RULE's totality, not the")
        print("  data's completeness: every qualifying record is assigned a value because")
        print("  the qualifying set is defined as the records the rule applies to.")
        print("  It is NOT a coverage figure and must not be published beside ones that are.")
        return 1
    if rule_total is False and emitted:
        print("VERDICT: MEASUREMENT. The rule can return absent and some records carry it.")
        return 0
    print("VERDICT: INCONCLUSIVE -- see the parts above. Do not report either world.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
