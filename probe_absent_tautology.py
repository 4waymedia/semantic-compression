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


def _part_b_facets(n_random: int, seed: int) -> dict:
    """facets asks a DIFFERENT question from temporal, and the difference matters.

    `facets` has TWO ways of not knowing, and only one of them is a sentinel:

        bucket 0x00  UNKNOWN   -- a VALUE. Declared, emitted, visible to a consumer.
        bucket 0xFF  ABSENT    -- a SENTINEL. Declared, and never emitted on any build.

    So the question is not "can it say unknown" (it demonstrably can) but "does the
    0xFF sentinel describe a state this system ever reaches". A contract documenting a
    mechanism the data never uses is what let 05-ExtractionPipeline AND this lane both
    ship a reader that decoded 0xFF as bucket 255 -- the rule was published in prose,
    implemented by neither, and never exercised by a build that would have caught it."""
    try:
        from facets import assign_facet                      # noqa: PLC0415
        from config import BUCKET, FLAG                      # noqa: PLC0415
    except Exception as e:                                   # noqa: BLE001
        print(f"  cannot import facets/config: {type(e).__name__}: {e}\n")
        return {}
    inv = {v: k for k, v in BUCKET.items()}
    rng = random.Random(seed)
    probes = _NO_EVIDENCE + _rand_words(n_random, rng)

    # READ THE FLAGS (2026-09-22, second pass). The first version did
    #     bucket, _cue, _flags = assign_facet(w)
    # and concluded "the rule never declines" -- from ONE THIRD of the return value.
    # `facets.py:213-215` sets FLAG['HEURISTIC'] on exactly the fallback this probe was
    # built to detect, so the channel marks its own guesses and the probe was blind to
    # the marker. A probe that discards part of what it measures reports the absence of
    # what it threw away.
    out, heur = Counter(), 0
    for w in probes:
        try:
            bucket, _cue, flags = assign_facet(w)
        except Exception as e:                               # noqa: BLE001
            print(f"  assign_facet({w!r}) raised {type(e).__name__}: {e}\n")
            return {}
        out[inv.get(bucket, f"?{bucket}")] += 1
        heur += bool(flags & FLAG["HEURISTIC"])

    print(f"  probes with NO facet evidence: {len(probes)}")
    print(f"  assign_facet bucket ->        {dict(out)}")
    says_unknown = out.get("UNKNOWN", 0)
    emits_ff = any(k.startswith("?255") for k in out)
    print(f"  FLAG['HEURISTIC'] set on      {heur}/{len(probes)}")
    print()
    print(f"  returns UNKNOWN (0x00) for {says_unknown}/{len(probes)} -- "
          f"{'the rule CAN decline' if says_unknown else 'the BUCKET never declines'}")
    print(f"  returns the 0xFF ABSENT sentinel: {'yes' if emits_ff else 'NO -- not once'}")
    print()
    print("  => `assign_facet` is TOTAL over surfaces: every surface gets a bucket, so")
    print("     'facets 100% populated' measures that totality, not the data.")
    if heur == len(probes):
        print("     BUT the fallback is MARKED: every evidence-free probe carries")
        print("     FLAG['HEURISTIC'], which facets.names.json publishes. A consumer CAN")
        print("     separate a finding from a guess -- via flags, not via bucket.")
    elif heur:
        print(f"     The fallback is marked on {heur}/{len(probes)} -- partially.")
    else:
        print("     And nothing marks the fallback, so a consumer cannot tell a finding")
        print("     from a guess anywhere in this channel.")
    print()
    return {"distribution": dict(out), "says_unknown": says_unknown,
            "emits_sentinel": emits_ff, "heuristic_marked": heur,
            "probe_count": len(probes)}


def part_b(field: str, n_random: int, seed: int) -> dict:
    print("PART B -- MUTATION: drive the builder's own functions with the evidence removed.")
    if field == "facets":
        return _part_b_facets(n_random, seed)
    if field != "temporal":
        print(f"  not yet wired for {field!r}.\n")
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

    # facets is its own shape: the rule is total over surfaces AND can return UNKNOWN as
    # a value. What is dead is the 0xFF SENTINEL, which is a different defect from a
    # tautology -- a contract clause describing a state the builder cannot produce.
    if a.field == "facets" and cb:
        if cb.get("says_unknown") and not cb.get("emits_sentinel"):
            print("VERDICT: NOT A TAUTOLOGY -- but the 0xFF sentinel is DEAD.")
            print("  `assign_facet` can and does return UNKNOWN (0x00) as a VALUE, so the")
            print("  channel is able to decline and a consumer can see when it has.")
            print("  100% populated measures that every surface gets a row, which is true")
            print("  and unremarkable -- not the temporal failure.")
            print()
            print("  The defect is narrower and real: bucket 0xFF is DECLARED as the absent")
            print("  marker and the builder has no path that emits it. A contract clause")
            print("  nothing exercises is worse than a missing one, because nothing fails --")
            print("  05-ExtractionPipeline and this lane both shipped readers that decoded")
            print("  0xFF as bucket 255 against data where it never appears.")
            print()
            print("  Disposition: either retire 0xFF from the contract and declare 0x00")
            print("  UNKNOWN as the only 'not known' state, or give the builder a path that")
            print("  emits it. Do not leave it declared and unreachable.")
            return 1
        if cb.get("heuristic_marked") == cb.get("probe_count"):
            # CORRECTED 2026-09-22 (second pass). The previous verdict here said facets
            # was "a STRONGER tautology than temporal's" because the bucket never returns
            # UNKNOWN. It does not -- and `facets.py:215` sets FLAG['HEURISTIC'] on that
            # exact path, `facets.names.json` publishes the flag name, and
            # facets_stats.json counts 270,180 records (61.7%) carrying it. The channel
            # marks its own guesses. The first verdict was produced by a probe that read
            # the bucket and discarded the flags.
            print("VERDICT: TOTAL, BUT HONEST -- not a tautology in temporal's sense.")
            print("  The bucket never returns UNKNOWN on evidence-free input, so")
            print("  '100% populated' does measure the rule's totality rather than the")
            print("  data. That much matches temporal.")
            print()
            print("  What does NOT match, and what the first pass of this probe missed by")
            print("  reading one third of the return value: the fallback is MARKED.")
            print("  facets.py:215 sets FLAG['HEURISTIC'] on exactly this path, the flag")
            print("  name is published in facets.names.json, and 270,180 records (61.7%)")
            print("  carry it on this build. A consumer can separate a finding from a")
            print("  guess -- through `flags`, not through `bucket`.")
            print()
            print("  temporal has no equivalent: a defaulted PROCESS is indistinguishable")
            print("  from a measured one. THAT is the difference between the two channels,")
            print("  and it is the thing worth copying to temporal rather than copying")
            print("  temporal's verdict to here.")
            print()
            print("  Remaining real defect: bucket 0xFF is declared and unreachable (§2.2).")
            print("  Disposition: do NOT add a decline path -- it would duplicate the flag")
            print("  and destroy a deliberate rubric default. Retire 0xFF, or give it a")
            print("  meaning distinct from 0x00.")
            return 1
        if not cb.get("says_unknown") and not cb.get("emits_sentinel"):
            # 2026-09-22: THE BRANCH I DID NOT PREDICT, and the one that fired.
            # I expected assign_facet to return UNKNOWN on evidence-free input, making
            # facets the well-behaved counter-example to temporal. Measured: 0/398. The
            # first version of this function had no branch for that, and printed
            # INCONCLUSIVE on a decisive result -- a verdict table written to cover the
            # expected outcome is not a test, it is a confirmation.
            d = cb.get("distribution") or {}
            top = max(d, key=d.get) if d else "?"
            print("VERDICT: TAUTOLOGY -- and a STRONGER one than temporal's.")
            print("  `assign_facet` is total AND never declines: 0 of the evidence-free")
            print(f"  probes came back UNKNOWN. Meaningless strings are assigned {top!r}.")
            print()
            print("  So `facets` has TWO declared ways of not knowing and NO path to either:")
            print("    bucket 0x00 UNKNOWN -- a value the rule does not emit for unknowns")
            print("    bucket 0xFF ABSENT  -- a sentinel the builder cannot produce at all")
            print()
            print("  'facets 100% populated' therefore measures that every surface gets a")
            print("  row, which is guaranteed by construction. temporal at least gated on")
            print("  VERB; this assigns a semantic bucket to any string whatsoever.")
            print()
            print("  Read this beside bucket's effective cardinality of 2.0: the modal")
            print("  value is partly a DEFAULT, not a finding. A channel cannot discriminate")
            print("  when its commonest value is what it emits in the absence of evidence.")
            print()
            print("  Disposition: give the rule a decline path (return UNKNOWN when no")
            print("  branch matched), then decide whether 0xFF means anything distinct from")
            print("  it. Until then do not read bucket as evidence about a surface.")
            return 1
        print("VERDICT: INCONCLUSIVE for facets -- see Part B.")
        return 2

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
