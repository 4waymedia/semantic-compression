"""The shipped verbs. Each case is a bug two adapters actually shipped.

Every assertion here corresponds to a real failure, in a real lane, in the last two
weeks. This file is not coverage — it is the list of mistakes the API exists to make
unwritable, kept in the form that fails if the API stops preventing them.

    text() vs words()   two adapters space-joined lowercase surfaces and called it text
    is_proper()         `if wc["proper"]` fired on proper==1, which means NOT a name
    explain_field()     an adapter ranked salience by `bucket`; 99.9% of v04 is 2 values
    require=            a consumer read a missing channel as "no data" for six weeks

    python tests/test_dictionary_api.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "elo-dictionary" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "elo-compression" / "src"))

from compression_dictionary import (open_dictionary,          # noqa: E402
                                    ChannelUnavailable)


def run() -> int:
    fails = 0

    def check(label, got, want):
        nonlocal fails
        ok = got == want
        fails += (not ok)
        print(f"  {'ok ' if ok else 'FAIL'} {label:46}{got!r}"
              f"{'' if ok else f'   want {want!r}'}")

    d = open_dictionary()
    print(f"open_dictionary() -> {d.build}  fp={(d.fingerprint or '')[:16]}")
    print(f"  channels: {', '.join(d.channels)}\n")

    # --- THE bug: text() and words() must differ, and both must be right ----
    print("text() vs words() -- the SDM/browser reconstruction bug:")
    ids = ["g:EIR"]                       # 'London' -- caps mask fused to the id
    check("words(['g:EIR'])  lowercase, for analysis", d.words(ids), ["london"])
    try:
        check("text(['g:EIR'])   AS AUTHORED", d.text(ids), "London")
    except Exception as e:
        print(f"  SKIP text(): {type(e).__name__} -- codec unavailable here")

    # a multi-token case where a naive ' '.join is visibly wrong
    print("\n  the naive join, side by side:")
    try:
        multi = ["QZe", "CuO", "g:EIR", "rYD"]
        auth = d.text(multi)
        naive = " ".join(d.words(multi))
        print(f"    text()          -> {auth!r}")
        print(f"    ' '.join(words) -> {naive!r}")
        print(f"    {'DIFFER (as they must)' if auth != naive else 'IDENTICAL -- suspicious'}")
    except Exception as e:
        print(f"    SKIP: {type(e).__name__}")

    # --- ELO-Browser's two defects, 2026-09-08. Permanent cases. -----------
    print("\nwords() -- the two defects the conformance fixture found:")
    ids_oov = d.ids_of("Nell read a verbalizer log.")
    check("A: OOV word survives ('verbalizer' is IN the id)",
          "verbalizer" in d.words(ids_oov), True)
    check("A: nothing silently dropped", len(d.words(ids_oov)), 5)
    ids_ph = d.ids_of("He said (the river) was cold.")
    check("B: len(words) is a WORD COUNT, not a token count",
          len(d.words(ids_ph)), 6)
    check("B: no whitespace tokens", any(not w.strip() for w in d.words(ids_ph)), False)
    check("surfaces() still carries the raw run", len(d.surfaces(ids_ph)), 8)

    print("\nids_of() -- the encode verb (was missing entirely):")
    for s in ("Nell read a verbalizer log.", "He said (the river) was cold.",
              "NASA sent iPhone data"):
        check(f"round-trip {s[:26]!r}", d.text(d.ids_of(s)), s)

    print("\nassets() -- one verb per channel, no hand-decoding:")
    a = d.assets("happy")
    check("assets('happy') has epa", a.get("epa") is not None, True)
    check("assets('happy') vfacets carries polarity_known",
          "polarity_known" in (a.get("vfacets") or {}), True)
    check("epa('london') is None, not NaN", d.epa("london"), None)

    # --- the truthiness trap ------------------------------------------------
    print("\nis_proper() -- `if wc['proper']` inverted a consumer's gate:")
    for surf, want in (("israel", True), ("dog", False), ("abate", False)):
        check(f"is_proper({surf!r})", d.is_proper(surf), want)
    raw = d.wordclass("dog")
    check("wordclass('dog')['proper'] is 1 == 'NO', and 1 is TRUTHY",
          (raw["proper"], bool(raw["proper"])), (1, True))

    # --- the fourth mistake: a field that cannot say it is useless ----------
    print("\nexplain_field() -- what data cannot say about itself:")
    for f, expect_ok in (("bucket", False), ("wordclass", True)):
        r = d.explain_field(f)
        check(f"explain_field({f!r}).discriminates", r.discriminates, expect_ok)
        for line in str(r).splitlines():
            print(f"       {line}")

    # --- 2026-09-10: the field that caused the bug could not be asked about --
    print("\nexplain_field() -- the two fields that raised KeyError:")
    # `utility` IS the field ELO-Browser's salience ranking needed. It lived inside
    # `flags`, which reports the packed byte, so the one field that would have said
    # "99.85% CONTENT, effective 1.01, do not rank by me" was unreachable.
    r = d.explain_field("utility")
    check("utility is askable at all", r.distinct_values > 0, True)
    check("utility names its modal value", r.top[0][0], "CONTENT")
    check("utility does not discriminate", r.discriminates, False)
    for line in str(r).splitlines():
        print(f"       {line}")
    # polarity's ABSENCE is not polarity == 0. NEUTRAL is 0b00 and measured.
    pk = d.explain_field("polarity_known")
    pol = d.explain_field("polarity")
    check("polarity_known is askable", pk.distinct_values, 2)
    check("populated(polarity) == MEASURED share, not non-zero share",
          round(pol.populated_pct, 1), round(pk.top[0][1], 1))
    check("...and NEUTRAL is reported as a VALUE of polarity",
          "NEUTRAL" in [n for n, _ in pol.top], True)
    for line in str(pol).splitlines():
        print(f"       {line}")

    # --- 2026-09-10 §4: an unmeasured channel is no number, not a low one ----
    print("\ncoverage() -- how much of the vocabulary each channel reaches:")
    cov = {c.channel: c for c in d.coverage()}
    check("epa coverage is reported, not assumed",
          round(cov["epa"].pct_records, 2), 54.03)
    # 2026-09-22: the number above is correct and was SILENT ABOUT ITS BASE, which is the
    # whole heterogeneous id space -- words, phrases, names, symbols, web-structure,
    # numerics. `epa` over a symbol is absent BY DESIGN, so 54.03 understates a channel
    # that is 57.2% where it is expected at all. A consumer ranking channel reliability by
    # this figure gets it wrong, and nothing in the report said so.
    check("pct_records names what it is a percentage OF",
          "every type pooled" in cov["epa"].denominator, True)
    check("...and says the pooled figure UNDERSTATES a sparse channel",
          "UNDERSTATES" in cov["epa"].denominator, True)
    check("...and points at where the per-segment figures live",
          "coverage_census.json" in cov["epa"].denominator, True)
    check("...and that they are not in the bundle",
          "build-local" in cov["epa"].denominator, True)
    check("str(report) carries the denominator, not just the number",
          "of 437,995 ids" in str(cov["epa"]), True)
    # NaN is the DECLARED absent marker and it is never used on v04 -- absence is
    # expressed as a missing row instead. Both read as None; only this says which.
    check("epa: absent means NO ROW, not NaN, on this build",
          cov["epa"].measured, cov["epa"].records)
    check("vfacets has no channel-level measured count",
          cov["vfacets"].measured, None)
    # `templates` IS NOT ASSUMED PRESENT. v04 carried an empty sub-db; elo-v5 does not
    # create one at all, which is the better behaviour -- a channel nobody builds should
    # not exist rather than exist empty. The build-independent assertion is the REGISTRY's
    # declaration; the channel's presence is a property of the build in hand.
    from compression_dictionary import BY_NAME                  # noqa: PLC0415
    check("registry records WHY templates is unbuilt",
          bool(BY_NAME["templates"].unbuilt_reason), True)
    if "templates" in cov:
        check("...and where it exists it is EMPTY", cov["templates"].records, 0)
    else:
        print("  ok   templates absent from this build (not created, not empty)")
    for c in ("epa",) + (("templates",) if "templates" in cov else ()):
        for line in str(cov[c]).splitlines():
            print(f"       {line}")

    # --- 2026-09-14: the absent SENTINEL, honoured by the verb ----------------
    #
    # 05-ExtractionPipeline found this in their own code -- an absent facet record read
    # as `bucket=255`, a bucket id that does not exist, handed to consumers as a value;
    # and the mask bytes of that same absent record ORed in as logic cues the dictionary
    # never asserted. THIS READER HAD THE SAME BUG. The rule was published in prose
    # (BUNDLE.json `gate.absent`) and not implemented in the verb that exists so consumers
    # do not have to implement it.
    print("\nfacets() -- 0xFF is ABSENT, 0x00 is a MEASURED UNKNOWN:")
    from compression_dictionary.config import BUCKET            # noqa: PLC0415
    check("BUCKET has no 255 -- it is a sentinel, not a value",
          255 in set(BUCKET.values()), False)
    _f = d.facets("happy")
    check("a present record says absent=False", _f and _f.get("absent"), False)
    check("...and carries a real bucket name", isinstance(_f.get("bucket_name"), str),
          True)
    # explain_field must score absence the same way, in both directions.
    _b = d.explain_field("bucket")
    check("explain_field('bucket') never reports 255 as a value",
          any(n == "255" for n, _ in _b.top), False)
    check("...and UNKNOWN (0x00) counts as MEASURED, not absent",
          _b.populated_pct > 99.0, True)

    # --- absence raises ------------------------------------------------------
    print("\nrequire= -- a missing channel is a WRONG BUILD, not empty data:")
    try:
        open_dictionary(require=("morphology",))
        print("  FAIL require=('morphology',) did not raise")
        fails += 1
    except ChannelUnavailable:
        print("  ok  require=('morphology',) raised ChannelUnavailable")

    d.close()
    print(f"\n{'FAILURES: %d' % fails if fails else 'all API cases pass'}")
    return 1 if fails else 0


def test_api():
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
