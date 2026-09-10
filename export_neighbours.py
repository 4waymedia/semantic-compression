"""
export_neighbours.py -- emit the browser's NEIGHBOURS asset (the denotation channel)
from a dictionary build, as a binary CSR map INDEXED BY THE VOCAB INDEX `n`.

Sibling of export_browser_assets.py. Where `epa.bin`/`facets.bin` give affect and
affordance per `n`, `neighbours.bin` gives DENOTATION: the k nearest words by meaning
(768-d all-mpnet-base-v2 cosine), stored as neighbour `n`s so three channels share one
vocabulary index. `car`'s neighbours are `truck/vehicle/automobile` — the thing EPA
cannot express (EPA put `car` next to `credentials`).

Source of truth (never assumed):
    vocab        <build>.browser.json  -> entries[i] = {surface, id, n=i}, content_size
    fingerprint  dictionary.lmdb meta db  key b"dictionary_fingerprint"  (SAME as epa/facets)
    neighbours   the finalized 768-d denotative index (denotative_index.DenotativeIndex)

Wire format (matches export_browser_assets header exactly, then a CSR body):
    magic        8 bytes   b"ELONBR\\x01\\x00"
    count        u32 LE    == content_size
    fp_len       u32 LE    == 64
    fingerprint  64 bytes  ascii hex, the build's dictionary_fingerprint      (header = 80 B)
    offsets      (count+1) x u32 LE   CSR: neighbours of n = records[off[n] : off[n+1]]
    records      total    x (u32 neighbour_n LE + u8 sim)   5 bytes each
                 sim = round(cosine * 255)  in [0,255]
    A node with no neighbours has off[n] == off[n+1] (empty list). Zero-parse for Rust:
    bounds-checked reads out of include_bytes!, no allocation.

Filters (spec-asset-pipeline.md §3): only CONTENT surfaces carry neighbours (a surface
absent from the denotative index -> empty list); prune cosine < --min-sim (default
0.35); dedupe neighbours by lowercase (keep highest sim); drop a neighbour whose
lowercase == the query's; cap at --k (default 16). Neighbour surfaces not present in
this build's vocab are skipped (can't be keyed to an `n`).

Run (PowerShell, from repo root), AFTER the denotative index is built on THIS build:
    python semantic_compression\\denotative_index.py embed    --meta db\\builds\\<b>\\meta.db --out db\\builds\\<b> --device cuda
    python semantic_compression\\denotative_index.py finalize --meta db\\builds\\<b>\\meta.db --out db\\builds\\<b>
    python ELO-Browser\\tools\\export_neighbours.py --build semantic_compression\\db\\builds\\<b> --index semantic_compression\\db\\builds\\<b>
    python ELO-Browser\\tools\\export_neighbours.py --selftest   # packing round-trip, no deps
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import struct
import sys
from pathlib import Path

MAGIC_NBR = b"ELONBR\x01\x00"
HEADER = struct.Struct("<8sII")     # magic, count, fp_len  (16 B) + 64 fp = 80
OFF = struct.Struct("<I")           # CSR offset, u32
REC = struct.Struct("<IB")          # neighbour_n u32 + sim u8 = 5 B
DEFAULT_K = 16
DEFAULT_MIN_SIM = 0.35


# ---------------------------------------------------------------------------
# Pure packing / parsing (no heavy deps -- unit-testable without faiss)
# ---------------------------------------------------------------------------

def _header(count: int, fp: str) -> bytes:
    fp_b = fp.encode("ascii")
    if len(fp_b) != 64:
        fp_b = fp_b.ljust(64, b"0")[:64]
    return HEADER.pack(MAGIC_NBR, count, 64) + fp_b


def build_lists(entries, surface_to_n, neighbours_of, *, k=DEFAULT_K,
                min_sim=DEFAULT_MIN_SIM, pool=64):
    """For each vocab entry (in n order) produce [(neighbour_n, sim_u8), ...].

    neighbours_of(surface, pool) -> [(surface, cosine)] DESCENDING (as the denotative
    index returns). Injected so this is testable without faiss."""
    lists = []
    for e in entries:
        s = e["surface"]
        ql = s.lower()
        seen: set[str] = set()
        out: list[tuple[int, int]] = []
        for nsurf, cos in neighbours_of(s, pool):
            if cos < min_sim:
                break                       # descending -> no later one qualifies
            kl = nsurf.lower()
            if kl == ql or kl in seen:
                continue                    # drop self-casings + case dupes
            nn = surface_to_n.get(nsurf)
            if nn is None:
                continue                    # neighbour not in this build's vocab
            seen.add(kl)
            sim = 255 if cos >= 1.0 else (0 if cos < 0 else round(cos * 255))
            out.append((nn, sim))
            if len(out) >= k:
                break
        lists.append(out)
    return lists


def build_lists_batched(entries, surface_to_n, idx, *, k=DEFAULT_K,
                        min_sim=DEFAULT_MIN_SIM, pool=64, chunk=8192):
    """Fast path: ONE batched faiss search per chunk instead of a call per surface.
    Reconstructs all index vectors once, then searches the covered query matrix in
    chunks. Same filters/output as build_lists. Needs numpy + faiss (via idx)."""
    import numpy as np                                    # noqa: PLC0415
    lists = [[] for _ in entries]
    q_rows, q_ns = [], []
    for e in entries:
        i = idx._idx.get(e["surface"])
        if i is not None:
            q_rows.append(i); q_ns.append(e["n"])
    if not q_rows:
        return lists
    allv = idx.index.reconstruct_n(0, idx.index.ntotal)   # (N,768) f32, one shot
    Q = np.ascontiguousarray(allv[q_rows])
    for c0 in range(0, len(q_rows), chunk):
        c1 = min(c0 + chunk, len(q_rows))
        sims, ids = idx.index.search(Q[c0:c1], pool + 1)   # descending cosine
        for r in range(c0, c1):
            n = q_ns[r]; self_row = q_rows[r]
            ql = entries[n]["surface"].lower(); seen = set(); out = []
            for j, cos in zip(ids[r - c0], sims[r - c0]):
                if j < 0 or j == self_row:
                    continue
                if cos < min_sim:
                    break
                nsurf = idx.surfaces[j]; kl = nsurf.lower()
                if kl == ql or kl in seen:
                    continue
                nn = surface_to_n.get(nsurf)
                if nn is None:
                    continue
                seen.add(kl)
                out.append((nn, 255 if cos >= 1.0 else (0 if cos < 0 else round(cos * 255))))
                if len(out) >= k:
                    break
            lists[n] = out
    return lists


def pack_neighbours(count: int, lists, fingerprint: str) -> tuple[bytes, int]:
    """Assemble the full byte image (header + CSR). Returns (blob, total_records)."""
    if len(lists) != count:
        raise ValueError(f"lists {len(lists)} != count {count}")
    offsets = bytearray(OFF.size * (count + 1))
    recs = bytearray()
    cum = 0
    for i in range(count):
        OFF.pack_into(offsets, i * OFF.size, cum)
        for nn, sim in lists[i]:
            recs += REC.pack(nn, sim)
            cum += 1
    OFF.pack_into(offsets, count * OFF.size, cum)
    blob = _header(count, fingerprint) + bytes(offsets) + bytes(recs)
    expect = 80 + OFF.size * (count + 1) + REC.size * cum
    if len(blob) != expect:
        raise AssertionError(f"blob {len(blob)} != expected {expect}")
    return blob, cum


def parse_neighbours(blob: bytes):
    """Reader (the Rust side's semantics). Returns (fingerprint, count, {n:[(n,sim)]})."""
    magic, count, fp_len = HEADER.unpack_from(blob, 0)
    if magic != MAGIC_NBR:
        raise ValueError(f"bad magic {magic!r}")
    fp = blob[16:16 + fp_len].decode("ascii")
    base = 80
    offs = [OFF.unpack_from(blob, base + i * OFF.size)[0] for i in range(count + 1)]
    rec0 = base + OFF.size * (count + 1)
    out = {}
    for n in range(count):
        row = []
        for r in range(offs[n], offs[n + 1]):
            nn, sim = REC.unpack_from(blob, rec0 + r * REC.size)
            row.append((nn, sim))
        if row:
            out[n] = row
    return fp, count, out


# ---------------------------------------------------------------------------
# Self-test (no faiss / no lmdb) -- validates packing, CSR, and every filter
# ---------------------------------------------------------------------------

def _selftest() -> int:
    entries = [{"surface": s, "n": i} for i, s in enumerate(
        ["car", "truck", "Truck", "vehicle", "the", "happy"])]
    s2n = {e["surface"]: e["n"] for e in entries}
    fake = {
        "car":     [("truck", 0.82), ("Truck", 0.80), ("vehicle", 0.78),
                    ("CAR", 0.99), ("the", 0.20), ("notinvocab", 0.9)],
        "truck":   [("car", 0.82), ("vehicle", 0.71)],
        "vehicle": [("car", 0.78), ("truck", 0.71)],
        "the":     [("a", 0.9)],                  # 'a' not in vocab -> skipped -> empty
        "happy":   [],                            # no neighbours
        "Truck":   [("car", 0.80)],
    }
    lists = build_lists(entries, s2n, lambda s, pool: fake[s], k=3, min_sim=0.35, pool=64)
    fp = "a" * 64
    blob, total = pack_neighbours(len(entries), lists, fp)
    rfp, rcount, parsed = parse_neighbours(blob)

    ok = True
    def check(cond, msg):
        nonlocal ok
        print(("  ok  " if cond else "  FAIL") + " " + msg); ok = ok and cond

    check(rfp == fp and rcount == 6, "header round-trips (fp + count)")
    car = parsed.get(0, [])
    check([nn for nn, _ in car] == [1, 3], "car -> truck(n1), vehicle(n3): CAR self-drop, "
          "Truck lowercase-dupe collapsed, 'the' pruned<0.35, OOV skipped")
    check(all(s >= round(0.35 * 255) for _, s in car), "sims are u8 = round(cos*255), pruned")
    check(4 not in parsed, "'the' -> empty (only neighbour OOV)")
    check(5 not in parsed, "'happy' -> empty (no neighbours)")
    check(len(build_lists(entries, s2n, lambda s, p: fake["car"], k=2, min_sim=0.35)[0]) == 2,
          "k cap honoured")
    check(len(blob) == 80 + 4 * (6 + 1) + 5 * total, "blob size == header + CSR table + records")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _find_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "semantic_compression").is_dir() and (p / "Memory").is_dir():
            return p
    raise SystemExit("cannot locate R-D-concepts root")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--build", type=Path, help="package dir (has <name>.browser.json + dictionary.lmdb)")
    ap.add_argument("--index", type=Path, help="dir holding the finalized denotative index (default: --build)")
    ap.add_argument("--out", type=Path, help="output dir for neighbours.bin")
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--min-sim", type=float, default=DEFAULT_MIN_SIM)
    ap.add_argument("--pool", type=int, default=64, help="candidate pool per query before filtering")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="packing round-trip; no deps")
    a = ap.parse_args()

    if a.selftest:
        return _selftest()
    if not a.build:
        raise SystemExit("--build is required (or use --selftest)")

    root = _find_root(Path(__file__).resolve())
    sys.path.insert(0, str(root / "semantic_compression"))
    index_dir = a.index or a.build
    out_dir = a.out or (root / "ELO-Browser" / "elo-browser" / "src-tauri" / "dictionary" / a.build.name)

    vocab_path = a.build / f"{a.build.name}.browser.json"
    lmdb_path = a.build / "dictionary.lmdb"
    for p in (vocab_path, lmdb_path):
        if not p.exists():
            raise SystemExit(f"missing: {p}")

    vocab = json.loads(vocab_path.read_text(encoding="utf-8"))
    entries = vocab["entries"]
    count = vocab["content_size"]
    if len(entries) != count:
        raise SystemExit(f"entries {len(entries)} != content_size {count}")
    for i, e in enumerate(entries):
        if e["n"] != i:
            raise SystemExit(f"vocab index not contiguous at {i}: n={e['n']}")
    surface_to_n = {e["surface"]: e["n"] for e in entries}

    import lmdb                                        # noqa: PLC0415
    env = lmdb.open(str(lmdb_path), readonly=True, lock=False, max_dbs=16, subdir=True)
    meta = env.open_db(b"meta", create=False)
    with env.begin() as txn:
        fingerprint = (txn.get(b"dictionary_fingerprint", db=meta) or b"").decode() or "unknown"

    from denotative_index import DenotativeIndex       # noqa: PLC0415 (needs faiss)
    idx = DenotativeIndex.load(index_dir)
    covered = sum(1 for e in entries if idx.has(e["surface"]))
    print(f"vocab {a.build.name}: n=0..{count-1}   index covers {covered:,}/{count:,} vocab surfaces "
          f"(index has {len(idx.surfaces):,})")
    if covered < count * 0.5:                          # diagnose an unexpectedly low overlap
        miss = [e["surface"] for e in entries if not idx.has(e["surface"])][:20]
        print(f"  [diag] {count-covered:,} vocab surfaces NOT in index; sample: {miss}")
    print(f"fingerprint {fingerprint}   k={a.k} min_sim={a.min_sim}")

    # Batched faiss search (one matrix search per chunk) — ~10-50x faster than a
    # call per surface on a CPU flat index.
    lists = build_lists_batched(entries, surface_to_n, idx,
                                k=a.k, min_sim=a.min_sim, pool=a.pool)
    blob, total = pack_neighbours(count, lists, fingerprint)
    with_nb = sum(1 for row in lists if row)
    print(f"neighbours.bin  {len(blob)/1e6:6.2f} MB   {total:,} records   "
          f"{with_nb:,}/{count:,} vocab entries have >=1 neighbour "
          f"(avg {total/max(with_nb,1):.1f})")

    if a.dry_run:
        print("--dry-run: nothing written")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "neighbours.bin").write_bytes(blob)
    h = hashlib.sha256(blob).hexdigest()[:16]
    print(f"  wrote {out_dir/'neighbours.bin'}  sha256:{h}")
    (out_dir / "neighbours.meta.json").write_text(json.dumps({
        # Derived, never typed -- see the note in export_browser_assets.py. The literal
        # here named a lane that no longer owns this script.
        "generated_by": str(Path(__file__).resolve().parent.name + "/"
                            + Path(__file__).name),
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "build": a.build.name, "dictionary_fingerprint": fingerprint,
        "vocab_entries": count, "records": total, "entries_with_neighbours": with_nb,
        "k": a.k, "min_sim": a.min_sim, "index_covers": covered,
        "neighbours_bin_sha256_16": h,
        "format": "CSR: 80B header + (count+1) u32 offsets + records(u32 n,u8 sim)",
    }, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
