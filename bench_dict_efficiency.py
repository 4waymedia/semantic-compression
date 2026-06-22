"""
bench_dict_efficiency.py -- dictionary compression efficiency on a held-out,
channel-balanced transcript eval set. Staged so each stage fits a short budget.

Eval set: the TOP-K largest transcripts per channel (ranked by JSON file size, a
fast proxy), then the concatenated chunk text of those is what gets compressed.
Efficiency = raw_text_bytes / encoded .eloB bytes, with a byte-exact round-trip
guard on a sample of files.

    python -m semantic_compression.bench_dict_efficiency select \
        --resources R-D-concepts/Resources/transcripts \
        --channels  semantic_compression/data/eval_channels.txt --top 10
    python -m semantic_compression.bench_dict_efficiency measure \
        --lmdb prod=semantic_compression/db/dictionary.lmdb
    python -m semantic_compression.bench_dict_efficiency report
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from semantic_compression.compressor import Compressor

WORK_ROOT = Path("semantic_compression/db/_testgroups/dict_eff")

def _work(source: str) -> Path:
    return WORK_ROOT / source

def _cache(source: str) -> Path:
    return _work(source) / "eval_cache.json"


def transcript_text(path: Path) -> str:
    d = json.loads(path.read_text(encoding="utf-8"))
    return " ".join((c.get("text") or "").strip() for c in d.get("chunks", []))


def cmd_select(args) -> None:
    eval_set = []
    if args.source == "books":
        bdir = Path(args.books)
        for f in sorted(bdir.glob("*.txt")):
            t = f.read_text(encoding="utf-8", errors="replace")
            if t.strip():
                eval_set.append({"channel": f.stem, "file": str(f),
                                 "raw_bytes": len(t.encode("utf-8")), "text": t})
                print(f"  {f.stem:<36} {f.stat().st_size/1e6:.2f} MB")
        unit = "books"
    else:
        channels = [ln.strip() for ln in Path(args.channels).read_text().splitlines()
                    if ln.strip() and not ln.startswith("#")]
        resources = Path(args.resources)
        for ch in channels:
            cdir = resources / ch
            if not cdir.is_dir():
                print(f"  [skip] {ch}: no dir"); continue
            files = sorted(cdir.glob("*.json"), key=lambda f: -f.stat().st_size)[:args.top]
            kept = 0
            for f in files:
                try:
                    t = transcript_text(f)
                except Exception:
                    continue
                if not t.strip():
                    continue
                eval_set.append({"channel": ch, "file": str(f),
                                 "raw_bytes": len(t.encode("utf-8")), "text": t})
                kept += 1
            print(f"  {ch:<26} {kept} files")
        unit = "transcripts"
    w = _work(args.source); w.mkdir(parents=True, exist_ok=True)
    _cache(args.source).write_text(json.dumps(eval_set), encoding="utf-8")
    n_ch = len({e["channel"] for e in eval_set})
    mb = sum(e["raw_bytes"] for e in eval_set) / 1e6
    print(f"\neval cache [{args.source}]: {len(eval_set)} {unit} / {n_ch} groups / {mb:.1f} MB -> {_cache(args.source)}")


def cmd_measure(args) -> None:
    eval_set = json.loads(_cache(args.source).read_text())
    # contiguous shard k of N so each call fits the time budget
    N = max(1, args.of); k = max(0, min(args.part, N - 1))
    per = (len(eval_set) + N - 1) // N
    eval_set = eval_set[k * per:(k + 1) * per]
    name, _, path = args.lmdb.partition("=")
    c = Compressor(lmdb_path=Path(path), preload_rev_cache=True,
                   preload_fwd_cache=True, preload_int_cache=False)
    c.open()
    per_ch: dict[str, list[int]] = {}
    tot_raw = tot_enc = n = 0
    rt_checked = rt_ok = 0
    t0 = time.time()
    for i, e in enumerate(eval_set):
        raw = e["text"].encode("utf-8")
        if not raw:
            continue
        enc = c.encode_bytes_binary(raw, ".txt")
        if i % args.rt_every == 0:                 # sampled round-trip guard
            _ext, back = c.decode_bytes_binary(enc)
            rt_checked += 1; rt_ok += (back == raw)
        a = per_ch.setdefault(e["channel"], [0, 0])
        a[0] += len(raw); a[1] += len(enc)
        tot_raw += len(raw); tot_enc += len(enc); n += 1
    c.close()
    res = {"name": name, "lmdb": path, "raw": tot_raw, "enc": tot_enc,
           "ratio": tot_raw / tot_enc if tot_enc else 0, "n_files": n,
           "rt_checked": rt_checked, "rt_ok": rt_ok, "secs": round(time.time() - t0, 1),
           "per_channel": {k: {"raw": v[0], "enc": v[1], "ratio": v[0]/v[1] if v[1] else 0}
                           for k, v in per_ch.items()}}
    (_work(args.source) / f"result_{name}_p{k}of{N}.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(f"[{name} p{k}/{N}] files={n} ratio={res['ratio']:.3f} "
          f"raw={tot_raw/1e6:.2f}MB enc={tot_enc/1e6:.2f}MB "
          f"round-trip={rt_ok}/{rt_checked} {res['secs']}s")


def cmd_report(args) -> None:
    shards = [json.loads(p.read_text()) for p in sorted(_work(args.source).glob("result_*.json"))]
    if not shards:
        print("no results"); return
    merged: dict = {}
    for sd in shards:
        m = merged.setdefault(sd["name"], {"name": sd["name"], "raw": 0, "enc": 0,
                              "n_files": 0, "rt_checked": 0, "rt_ok": 0, "secs": 0.0,
                              "per_channel": {}})
        m["raw"] += sd["raw"]; m["enc"] += sd["enc"]; m["n_files"] += sd["n_files"]
        m["rt_checked"] += sd["rt_checked"]; m["rt_ok"] += sd["rt_ok"]; m["secs"] += sd["secs"]
        for ch, v in sd["per_channel"].items():
            a = m["per_channel"].setdefault(ch, {"raw": 0, "enc": 0})
            a["raw"] += v["raw"]; a["enc"] += v["enc"]
    results = []
    for m in merged.values():
        m["ratio"] = m["raw"] / m["enc"] if m["enc"] else 0
        for ch, v in m["per_channel"].items():
            v["ratio"] = v["raw"] / v["enc"] if v["enc"] else 0
        results.append(m)
    results.sort(key=lambda r: r["name"])
    (_work(args.source) / "REPORT.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"{'dictionary':<14}{'files':>6}{'raw MB':>9}{'enc MB':>9}{'ratio':>8}{'round-trip':>12}{'sec':>7}")
    for r in results:
        print(f"{r['name']:<14}{r['n_files']:>6}{r['raw']/1e6:>9.2f}{r['enc']/1e6:>9.2f}"
              f"{r['ratio']:>8.3f}{(str(r['rt_ok'])+'/'+str(r['rt_checked'])):>12}{r['secs']:>7}")
    base = results[0]
    print(f"\nratio vs {base['name']}:")
    for r in results:
        print(f"  {r['name']:<14}{r['ratio']:.4f}  ({(r['ratio']/base['ratio']-1):+.2%})")


def main() -> None:
    p = argparse.ArgumentParser(description="Dictionary efficiency on a channel-balanced transcript eval set")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select"); s.add_argument("--source", default="transcripts", choices=("transcripts", "books"))
    s.add_argument("--resources", default="R-D-concepts/Resources/transcripts")
    s.add_argument("--books", default="R-D-concepts/Resources/books")
    s.add_argument("--channels", default="semantic_compression/data/eval_channels.txt")
    s.add_argument("--top", type=int, default=10); s.set_defaults(func=cmd_select)
    m = sub.add_parser("measure"); m.add_argument("--source", default="transcripts", choices=("transcripts", "books"))
    m.add_argument("--lmdb", required=True, metavar="NAME=PATH")
    m.add_argument("--rt-every", type=int, default=15, help="round-trip-check every Nth file")
    m.add_argument("--part", type=int, default=0); m.add_argument("--of", type=int, default=1)
    m.set_defaults(func=cmd_measure)
    r = sub.add_parser("report"); r.add_argument("--source", default="transcripts", choices=("transcripts", "books"))
    r.set_defaults(func=cmd_report)
    args = p.parse_args(); args.func(args)


if __name__ == "__main__":
    main()
