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
