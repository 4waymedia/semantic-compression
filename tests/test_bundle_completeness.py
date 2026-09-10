"""A bundle declares EVERY file it ships — and this proves the check can fail.

elo-browser-v04 shipped `neighbours.bin` (22 MB, the largest single asset) and
`vfacets.names.json` (the field contract declaring `polarity_known`) with no sha in any
manifest, while every integrity check reported OK. Three separate hand-maintained
"shipped files" lists existed and none of them was complete:

    export_browser_assets.py   assets.meta.json  -> 5 files   (written 2/3 through the
                                                               script, before
                                                               vfacets.names.json existed)
    export_neighbours.py       neighbours.meta.json -> itself only
    publish_dictionary.py      BUNDLE.json       -> 6 files   (omitted vfacets.names.json)

The failure is structural, not clerical: **a one-way integrity check cannot detect an
omission.** Walking `BUNDLE.json["files"]` and re-hashing each one proves that what was
declared is undamaged and says nothing about a file nothing declares. So the fix is to
ask the DIRECTORY, in both directions.

A gate that has never failed is a gate nobody has tested. These cases assert the
refusals, not the happy path.

    python semantic_compression/tests/test_bundle_completeness.py
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

SC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SC))

import publish_dictionary as pub                                   # noqa: E402


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _fake_bundle(tmp: Path, *, files: dict, declare: "set | None" = None) -> Path:
    """Write `files`, and a BUNDLE.json declaring `declare` (default: all of them)."""
    d = tmp / "bundle"
    d.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (d / name).write_bytes(data)
    names = sorted(declare if declare is not None else files)
    shas = {n: _sha(files[n]) for n in names}
    doc = {
        "schema": pub.SCHEMA, "build": "test-build", "display": "test",
        "status": "published",
        "bundle_fingerprint": _sha(
            "".join(f"{n}\t{s}\n" for n, s in sorted(shas.items())).encode()),
        "files": {n: {"sha256": shas[n], "bytes": len(files[n])} for n in names},
    }
    (d / "BUNDLE.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return d


def _verify(d: Path) -> tuple[int, str]:
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = pub._verify_published(d)
    except SystemExit as e:                     # noqa: PERF203
        return 2, f"{buf.getvalue()}\nSystemExit: {e}"
    return rc, buf.getvalue()


def run() -> int:
    fails = 0

    def check(label, got, want):
        nonlocal fails
        ok = got == want
        fails += (not ok)
        print(f"  {'ok ' if ok else 'FAIL'} {label:56}{got!r}"
              f"{'' if ok else f'   want {want!r}'}")

    payload = {"epa.bin": b"EPA-payload", "facets.bin": b"FCT-payload",
               "vfacets.names.json": b'{"version":2}'}

    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)

        print("BASELINE -- a complete bundle verifies:")
        d = _fake_bundle(tmp / "a", files=payload)
        rc, out = _verify(d)
        check("complete bundle -> rc 0", rc, 0)
        check("...and BUNDLE.json is not payload of itself",
              "BUNDLE.json" in pub.NOT_PAYLOAD, True)

        print("\nTHE v04 BUG -- a file ships that nothing declares:")
        # Exactly the v04 shape: the biggest asset present, absent from the manifest.
        d = _fake_bundle(tmp / "b", files=payload,
                         declare={"epa.bin", "facets.bin"})
        rc, out = _verify(d)
        check("undeclared payload -> rc 2 (REFUSED)", rc, 2)
        check("...and it names the file", "vfacets.names.json" in out, True)
        check("...as UNDECLARED, not as damage", "UNDECLARED" in out, True)
        # The old one-way check would have passed this bundle: everything DECLARED is
        # intact. That is the whole point.
        check("every declared file is in fact intact (the old check passed)",
              all(f"FAIL {n}" not in out for n in ("epa.bin", "facets.bin")), True)

        print("\nSTILL CATCHES DAMAGE -- the check it always did:")
        d = _fake_bundle(tmp / "c", files=payload)
        (d / "epa.bin").write_bytes(b"TAMPERED")
        rc, out = _verify(d)
        check("modified payload -> rc 2", rc, 2)
        check("...names the damaged file", "FAIL epa.bin" in out, True)

        print("\nAND MISSING FILES:")
        d = _fake_bundle(tmp / "d", files=payload)
        (d / "facets.bin").unlink()
        rc, out = _verify(d)
        check("declared-but-absent -> rc 2", rc, 2)
        check("...names it MISSING", "MISSING facets.bin" in out, True)

        print("\npayload_files() is directory-driven, not list-driven:")
        d = _fake_bundle(tmp / "e", files=payload)
        (d / "a_channel_invented_tomorrow.bin").write_bytes(b"future")
        got = set(pub.payload_files(d))
        check("a file nobody has heard of is still payload",
              "a_channel_invented_tomorrow.bin" in got, True)
        check("BUNDLE.json is excluded", "BUNDLE.json" in got, False)
        rc, out = _verify(d)
        check("...and it is REFUSED until declared", rc, 2)

    print(f"\n{'FAILURES: %d' % fails if fails else 'all bundle-completeness cases pass'}")
    return 1 if fails else 0


def test_bundle_completeness():
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
