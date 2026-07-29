"""
export_browser_vocab.py -- emit <build>.browser.json, the dense vocab index the
browser + neighbours assets key to.

WHY THIS EXISTS: the core build emits token-ids.csv.gz / special-tokens.json /
byte-fallback.csv / profile-cuts.json, but nothing emitted the `<name>.browser.json`
vocab file that export_browser_assets.py and export_neighbours.py both require. The
one shipped copy (elo-browser-v01) was produced out-of-band and drifted out of the
build. This restores it as a deterministic, in-cascade step (asset-pipeline stage 6).

It is a pure PROJECTION of token-ids.csv.gz: take the rows in the requested profile
cut (default `full`), and for each emit {surface, id: base64_id, n: int_id}. `n` is the
LLM integer id, which for the cut is a contiguous 0..content_size-1 (rows are written
in int_id order). Header numbers come from profile-cuts.json so this file and the
profile contract never disagree.

    python export_browser_vocab.py --build db/builds/<name>            # writes <name>.browser.json
    python export_browser_vocab.py --build db/builds/<name> --cut full
    python export_browser_vocab.py --selftest                          # projection logic, no deps

No heavy deps (gzip + csv + json). Atomic write.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
from pathlib import Path

CUTS = ("tiny", "compact", "standard", "full")


def project_entries(rows, cut: str):
    """rows: iterable of dicts from token-ids.csv.gz. Return entries in n order.

    A row is in the cut if its `<cut>` column == 'Y'. `n` = int(id) (the LLM integer
    id); for a prefix cut these are contiguous 0..N-1. We assert contiguity so a
    silently reordered token-ids can't produce a misaligned vocab."""
    entries = []
    for r in rows:
        if r.get(cut) != "Y":
            continue
        entries.append({"surface": r["surface"], "id": r["base64_id"], "n": int(r["id"])})
    entries.sort(key=lambda e: e["n"])
    for i, e in enumerate(entries):
        if e["n"] != i:
            raise ValueError(f"cut '{cut}' not contiguous at position {i}: n={e['n']} "
                             "(token-ids.csv.gz rows are not a frequency prefix)")
    return entries


def build_vocab(name: str, cut: str, entries: list, cut_meta: dict | None,
                dictionary_fingerprint: str | None = None) -> dict:
    content_size = len(entries)
    meta = cut_meta or {}
    # prefer profile-cuts numbers; fall back to deriving from content_size
    bfs = meta.get("byte_fallback_start", content_size)
    sts = meta.get("special_tokens_start", content_size)  # unknown fallback -> == bfs
    tot = meta.get("total_vocab", content_size)
    return {
        "vocab_version": name,
        # The family binding (DICTIONARY-FAMILY-INTEGRATION.md §2). Emitted as
        # "unknown" rather than omitted so a consumer can tell "this build could
        # not report one" from "this file predates the field".
        "dictionary_fingerprint": dictionary_fingerprint or "unknown",
        "cut": cut,
        "entry_count": content_size,
        "content_size": content_size,
        "byte_fallback_start": bfs,
        "special_tokens_start": sts,
        "total_vocab": tot,
        "entries": entries,
    }


def read_dictionary_fingerprint(build_dir: Path) -> str | None:
    """The build's `dictionary_fingerprint`, or None when it cannot be read.

    SAME SOURCE as ELO-Browser/tools/export_browser_assets.py:154 — the
    dictionary.lmdb `meta` sub-DB, key `dictionary_fingerprint`. Deliberately
    not a second scheme: BINDINGS.md says "Have the producer emit ... whatever
    fingerprints it already computes. Do not invent a second hashing scheme."

    Soft import so this module keeps its no-heavy-deps promise (it is otherwise
    a pure gzip+csv+json projection). Missing lmdb, missing db, or missing key
    all yield None = UNKNOWN, which downstream must treat as "no conflict"
    rather than as a mismatch — the D4 guard semantics 06/07 already use.

    WHY THE VOCAB FILE NEEDS IT: DICTIONARY-FAMILY-INTEGRATION.md §2 requires
    the browser to verify the .bin family fingerprint "matches the codec
    dictionary you decode with". The codec vocab shipped without one, so that
    half of §2 was unverifiable — the assets could be checked against each
    other but never against the vocabulary they index."""
    try:
        import lmdb                                     # noqa: PLC0415
    except Exception:
        return None
    db = build_dir / "dictionary.lmdb"
    if not db.exists():
        return None
    try:
        env = lmdb.open(str(db), readonly=True, max_dbs=8, lock=False)
        try:
            meta = env.open_db(b"meta", create=False)
            with env.begin() as txn:
                raw = txn.get(b"dictionary_fingerprint", db=meta)
            return raw.decode() if raw else None
        finally:
            env.close()
    except Exception:
        return None


def _read_token_ids(path: Path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as gz:
        yield from csv.DictReader(gz)


def _atomic_write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    os.replace(tmp, path)


def emit(build_dir: Path, cut: str = "full") -> dict:
    name = build_dir.name
    token_ids = build_dir / "token-ids.csv.gz"
    if not token_ids.exists():
        raise SystemExit(f"missing {token_ids} — run the core build first")
    cuts_path = build_dir / "profile-cuts.json"
    cut_meta = None
    if cuts_path.exists():
        cut_meta = json.loads(cuts_path.read_text(encoding="utf-8")).get(cut)
    entries = project_entries(_read_token_ids(token_ids), cut)
    fingerprint = read_dictionary_fingerprint(build_dir)
    vocab = build_vocab(name, cut, entries, cut_meta, fingerprint)
    out = build_dir / f"{name}.browser.json"
    _atomic_write_json(out, vocab)
    print(f"wrote {out.name}: cut={cut} content_size={vocab['content_size']:,} "
          f"total_vocab={vocab['total_vocab']:,} "
          f"fingerprint={vocab['dictionary_fingerprint'][:16]}")
    return vocab


# ---------------------------------------------------------------------------

def _selftest() -> int:
    rows = [
        {"id": "0", "base64_id": "gA", "surface": "the",   "full": "Y", "standard": "Y"},
        {"id": "1", "base64_id": "gB", "surface": "car",   "full": "Y", "standard": "N"},
        {"id": "2", "base64_id": "gC", "surface": "murder","full": "Y", "standard": "Y"},
        {"id": "3", "base64_id": "gD", "surface": "rare",  "full": "N", "standard": "N"},
    ]  # standard cut = {the(n0), murder(n2)} -> non-contiguous on purpose
    ok = True
    def check(c, m):
        nonlocal ok; print(("  ok  " if c else "  FAIL") + " " + m); ok = ok and c

    full = project_entries(iter(rows), "full")
    check([e["surface"] for e in full] == ["the", "car", "murder"], "full cut = the/car/murder (rare dropped)")
    check([e["n"] for e in full] == [0, 1, 2], "n contiguous 0..2")
    check(full[1] == {"surface": "car", "id": "gB", "n": 1}, "entry shape {surface,id,n}")
    v = build_vocab("t", "full", full, {"byte_fallback_start": 3, "special_tokens_start": 259, "total_vocab": 275})
    check(v["content_size"] == 3 and v["total_vocab"] == 275, "header from profile-cuts")
    # non-contiguous cut must raise (standard skips n=2)
    try:
        project_entries(iter(rows), "standard"); check(False, "standard non-contiguous should raise")
    except ValueError:
        check(True, "non-contiguous cut raises (guards silent misalignment)")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--build", type=Path)
    ap.add_argument("--cut", default="full", choices=CUTS)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    if not a.build:
        raise SystemExit("--build is required (or --selftest)")
    emit(a.build, a.cut)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
