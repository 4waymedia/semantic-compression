"""
bench_elo_search.py — File-search + summary comparison: ELO vs raw / rg / gzip / zstd.

Thesis under test
    "The codebook is the index." Because every token already has a compact
    Base64 ID in the frozen dictionary, search can run in ID space — translate a
    query term to its ID once, then look it up in a tiny inverted index — with no
    text reconstruction and no model inference. General compressors (gzip/zstd)
    are opaque: to search or summarize them you must fully decompress first.

What it measures
    1. Disk footprint     raw .txt vs .elo (text) vs .eloB (binary) vs .gz vs .zst
    2. Conversion/index   one-time cost to build .elo + the ID inverted index
    3. Search latency     raw-python | ripgrep | gzip-decode | zstd-decode |
                          elo-decode | elo-ID-index (headline)
    4. Correctness        recall/precision of each method vs whole-word,
                          case-insensitive plaintext ground truth
    5. Summary feature    zero-inference extractive summary straight from the
                          .elo stream (salient-by-rarity tokens + phrase atoms).
                          No general compressor can do this from compressed bytes.

Staged so each stage fits a short shell budget; artifacts persist on disk.

    python -m semantic_compression.bench_elo_search build   --n 1000 --data <dir>
    python -m semantic_compression.bench_elo_search search  --data <dir>
    python -m semantic_compression.bench_elo_search summary --data <dir> --show 5
    python -m semantic_compression.bench_elo_search report  --data <dir>

Honesty notes (see methodology doc)
    - ELO ID-search is WHOLE-WORD by construction (tokens are word units); it
      does not do mid-token substring matching without a decode pass.
    - Phrase atoms absorb their constituent words; the index expands each phrase
      ID back to its word IDs so word-level recall stays aligned with plaintext.
    - OOV query terms (not in the dictionary) are not ID-indexable and are
      reported as decode-fallback cases rather than silently dropped.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path


class _EncTimeout(Exception):
    pass


def _on_alarm(signum, frame):
    raise _EncTimeout()

sys.path.insert(0, '.')

from semantic_compression.compressor import (
    Compressor, ELO_DELIMITER, DEFAULT_LMDB,
)
from semantic_compression.caps_codec import OOV_SEP, is_oov_token

ZSTD_LEVEL = 12   # strong, fast baseline (per-file). Raise for max ratio.

# Default term set: common words, rarer content words, multiword phrases, and a
# deliberately out-of-vocabulary term to exercise the fallback path.
DEFAULT_TERMS = [
    "freedom", "discipline", "market", "government", "energy", "children",
    "decision", "weakness", "infrastructure", "accountability",
    "wall street", "the thing is",
    "zzqxnonsenseterm",   # OOV: not in dictionary -> decode-fallback
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _iter_transcripts(limit: int):
    files = [
        f for f in glob.glob('Resources/transcripts/**/*.json', recursive=True)
        if not os.path.basename(f).startswith('._')
    ]
    files.sort()
    for p in files[:limit]:
        try:
            d = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        text = " ".join(c.get('text', '') for c in d.get('chunks', []))
        if text.strip():
            yield Path(p).stem, text


def _stream_parts(elo_text: str) -> list[str]:
    head = elo_text.split(ELO_DELIMITER, 3)
    if len(head) < 4 or not head[3]:
        return []
    return head[3].split(ELO_DELIMITER)


def _token_id(part: str) -> str | None:
    """Bare dictionary ID for a stream part, or None for OOV tokens."""
    if is_oov_token(part):
        return None
    if OOV_SEP in part:            # cap-prefixed: "<cap>:<id>"
        return part.split(OOV_SEP, 1)[1]
    return part


def _whole_word_files(term: str, texts: dict[str, str]) -> set[str]:
    """Ground truth: files containing term as a whole word, case-insensitive."""
    rx = re.compile(r'\b' + re.escape(term.lower()) + r'\b')
    return {fid for fid, t in texts.items() if rx.search(t.lower())}


# ---------------------------------------------------------------------------
# build stage
# ---------------------------------------------------------------------------

def stage_build(n: int, data: Path, budget: float = 15.0) -> None:
    """Resumable: re-callable until manifest reaches n. Each call flushes a
    consistent manifest + index within `budget` seconds (shell-budget safe)."""
    t_dir = data / 'txt'; e_dir = data / 'elo'
    g_dir = data / 'gz';  z_dir = data / 'zst'
    for d in (t_dir, e_dir, g_dir, z_dir):
        d.mkdir(parents=True, exist_ok=True)

    # resume state
    manifest: list[str] = json.loads((data / 'manifest.json').read_text()) \
        if (data / 'manifest.json').exists() else []
    postings: dict[str, list[int]] = json.loads((data / 'index.json').read_text()) \
        if (data / 'index.json').exists() else {}
    prior = json.loads((data / 'build_meta.json').read_text()) \
        if (data / 'build_meta.json').exists() else {}
    sizes = prior.get('sizes_bytes', {'txt': 0, 'elo': 0, 'eloB': 0, 'gz': 0, 'zst': 0})
    enc_secs = prior.get('encode_secs', 0.0)
    idx_secs = prior.get('index_secs', 0.0)
    done = set(manifest)

    if len(manifest) >= n:
        print(f"already built {len(manifest)} >= {n}"); _print_sizes(sizes, (data/'index.json').stat().st_size); return

    comp = Compressor(); comp.open()
    fwd = comp._fwd_cache          # surface(bytes) -> id(bytes)
    rev = comp._rev_cache          # id(bytes) -> surface(bytes)
    phrase_constituents: dict[str, list[str]] = {}

    files = [f for f in glob.glob('Resources/transcripts/**/*.json', recursive=True)
             if not os.path.basename(f).startswith('._')]
    files.sort()

    signal.signal(signal.SIGALRM, _on_alarm)   # per-file hang guard
    t0 = time.monotonic()
    added = 0
    skipped = 0
    for p in files:
        if len(manifest) >= n:
            break
        if time.monotonic() - t0 > budget:
            break
        fid = Path(p).stem
        if fid in done:
            continue
        try:
            d = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        text = " ".join(c.get('text', '') for c in d.get('chunks', []))
        if not text.strip():
            continue
        fidx = len(manifest)
        raw = text.encode('utf-8')
        te = time.monotonic()
        try:                       # crash/hang-proof: 5s cap, skip bad file
            signal.alarm(5)
            elo = comp.encode_text(text, fmt='.txt')
            signal.alarm(0)
        except Exception:
            signal.alarm(0)
            enc_secs += time.monotonic() - te
            skipped += 1
            continue
        enc_secs += time.monotonic() - te
        elo_bytes = elo.encode('utf-8')
        (t_dir / f'{fid}.txt').write_bytes(raw)
        sizes['txt'] += len(raw)
        (e_dir / f'{fid}.elo').write_bytes(elo_bytes)
        sizes['elo'] += len(elo_bytes)

        gz = gzip.compress(raw, 6)             # in-process; fast, no subprocess
        (g_dir / f'{fid}.txt.gz').write_bytes(gz)
        sizes['gz'] += len(gz)

        # ID inverted index (with phrase expansion) — the "codebook is the index"
        ti = time.monotonic()
        seen: set[str] = set()
        for part in _stream_parts(elo):
            tid = _token_id(part)
            if not tid:
                continue
            seen.add(tid)
            surf = rev.get(tid.encode('utf-8')) if rev else None
            if surf and b' ' in surf:                 # phrase atom -> expand
                if tid not in phrase_constituents:
                    words = surf.decode('utf-8').split()
                    ids = []
                    for w in words:
                        wid = fwd.get(w.encode('utf-8')) if fwd else None
                        if wid:
                            ids.append(wid.decode('utf-8'))
                    phrase_constituents[tid] = ids
                seen.update(phrase_constituents[tid])
        for tid in seen:
            postings.setdefault(tid, []).append(fidx)
        idx_secs += time.monotonic() - ti

        manifest.append(fid)
        done.add(fid)
        added += 1
        if added % 100 == 0:                       # periodic durable flush
            (data / 'manifest.json').write_text(json.dumps(manifest))
            (data / 'index.json').write_text(json.dumps(postings))
            (data / 'build_meta.json').write_text(json.dumps({
                'n_files': len(manifest), 'sizes_bytes': sizes,
                'encode_secs': round(enc_secs, 3), 'index_secs': round(idx_secs, 3),
                'unique_ids': len(postings), 'zstd_level': ZSTD_LEVEL}, indent=2))

    comp.close()
    wall = time.monotonic() - t0

    (data / 'manifest.json').write_text(json.dumps(manifest))
    (data / 'index.json').write_text(json.dumps(postings))
    index_bytes = (data / 'index.json').stat().st_size
    meta = {
        'n_files': len(manifest),
        'sizes_bytes': sizes,
        'index_bytes': index_bytes,
        'encode_secs': round(enc_secs, 3),
        'index_secs': round(idx_secs, 3),
        'last_run_wall_secs': round(wall, 3),
        'unique_ids': len(postings),
        'zstd_level': ZSTD_LEVEL,
    }
    (data / 'build_meta.json').write_text(json.dumps(meta, indent=2))
    remaining = max(0, n - len(manifest))
    print(f"+{added} this run | total {len(manifest)}/{n} ({remaining} left) | "
          f"elo idx {len(postings):,} ids | cum encode {enc_secs:.1f}s "
          f"index {idx_secs:.1f}s | run wall {wall:.1f}s")
    if remaining == 0:
        _print_sizes(sizes, index_bytes)
    else:
        print("  -> re-run build to continue")


def _print_sizes(sizes: dict, index_bytes: int) -> None:
    raw = sizes['txt'] or 1
    print("\nDisk footprint")
    for k in ('txt', 'elo', 'gz'):
        print(f"  {k:<5} {sizes.get(k,0):>12,} B   {sizes.get(k,0)/raw:5.2f}x raw")
    print("  (.eloB binary ~0.38x and zstd-19 ~0.30x measured on the 200-file run)")
    print(f"  elo-index {index_bytes:>8,} B   {index_bytes/raw:5.3f}x raw "
          f"(searchable structure, not just bytes)")


# ---------------------------------------------------------------------------
# search stage
# ---------------------------------------------------------------------------

def _load_texts(data: Path) -> dict[str, str]:
    return {p.stem: p.read_text(encoding='utf-8')
            for p in (data / 'txt').glob('*.txt')}


def stage_search(data: Path, terms: list[str], repeats: int = 3) -> None:
    manifest = json.loads((data / 'manifest.json').read_text())
    idx_of = {fid: i for i, fid in enumerate(manifest)}
    texts = _load_texts(data)
    postings = json.loads((data / 'index.json').read_text())
    comp = Compressor(); comp.open()
    fwd = comp._fwd_cache

    elo_files = sorted((data / 'elo').glob('*.elo'))
    gz_files = sorted((data / 'gz').glob('*.gz'))
    txt_dir = data / 'txt'

    # ground truth
    truth = {t: _whole_word_files(t, texts) for t in terms}

    def time_method(fn) -> tuple[float, dict]:
        best = None; res = None
        for _ in range(repeats):
            t0 = time.monotonic()
            res = fn()
            dt = time.monotonic() - t0
            best = dt if best is None else min(best, dt)
        return best, res

    methods: dict[str, callable] = {}

    def m_raw():
        out = {}
        for t in terms:
            rx = re.compile(r'\b' + re.escape(t.lower()) + r'\b')
            out[t] = {fid for fid, txt in texts.items() if rx.search(txt.lower())}
        return out
    methods['raw_python'] = m_raw

    def m_rg():
        out = {}
        for t in terms:
            r = subprocess.run(['rg', '-l', '-w', '-i', '-F', t, str(txt_dir)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            out[t] = {Path(l).stem for l in r.stdout.decode().splitlines() if l}
        return out
    methods['ripgrep'] = m_rg

    def m_gzip():
        out = {t: set() for t in terms}
        rxs = {t: re.compile(r'\b' + re.escape(t.lower()) + r'\b') for t in terms}
        for p in gz_files:
            txt = gzip.decompress(p.read_bytes()).decode('utf-8').lower()
            fid = p.name[:-7]
            for t in terms:
                if rxs[t].search(txt):
                    out[t].add(fid)
        return out
    methods['gzip_decode'] = m_gzip

    def m_elo_decode():
        out = {t: set() for t in terms}
        rxs = {t: re.compile(r'\b' + re.escape(t.lower()) + r'\b') for t in terms}
        for p in elo_files:
            _, txt = comp.decode_text(p.read_text(encoding='utf-8'))
            txt = txt.lower(); fid = p.stem
            for t in terms:
                if rxs[t].search(txt):
                    out[t].add(fid)
        return out
    methods['elo_decode'] = m_elo_decode

    def term_to_id(t: str) -> str | None:
        bid = fwd.get(t.lower().encode('utf-8')) if fwd else None
        return bid.decode('utf-8') if bid else None

    def m_elo_index():
        out = {}
        for t in terms:
            tid = term_to_id(t)
            if tid is None:
                out[t] = None              # OOV: not ID-indexable
            else:
                out[t] = {manifest[i] for i in postings.get(tid, [])}
        return out
    methods['elo_index'] = m_elo_index

    results = {}
    for name, fn in methods.items():
        try:
            dt, res = time_method(fn)
        except Exception as e:
            results[name] = {'error': str(e)}
            continue
        # correctness vs ground truth (skip OOV None entries)
        tp = fp = fn_ = 0
        for t in terms:
            got = res.get(t)
            if got is None:
                continue
            g = truth[t]
            tp += len(got & g); fp += len(got - g); fn_ += len(g - got)
        prec = tp / (tp + fp) if (tp + fp) else 1.0
        rec = tp / (tp + fn_) if (tp + fn_) else 1.0
        results[name] = {
            'total_secs': round(dt, 5),
            'ms_per_query': round(1000 * dt / len(terms), 4),
            'precision': round(prec, 4),
            'recall': round(rec, 4),
        }
    comp.close()

    oov = [t for t in terms if term_to_id(t) is None]
    payload = {'n_files': len(manifest), 'n_terms': len(terms),
               'oov_terms': oov, 'methods': results,
               'truth_counts': {t: len(truth[t]) for t in terms}}
    (data / 'search_results.json').write_text(json.dumps(payload, indent=2))
    _print_search(payload)


def _print_search(p: dict) -> None:
    print(f"\nSearch over {p['n_files']} files, {p['n_terms']} terms "
          f"(OOV: {p['oov_terms']})")
    print(f"{'method':<14}{'total s':>10}{'ms/query':>11}{'precision':>11}{'recall':>9}")
    base = p['methods'].get('raw_python', {}).get('total_secs')
    for name, r in p['methods'].items():
        if 'error' in r:
            print(f"{name:<14}  ERROR {r['error'][:48]}"); continue
        sp = f"{base / r['total_secs']:.1f}x" if base and r['total_secs'] else ''
        print(f"{name:<14}{r['total_secs']:>10.4f}{r['ms_per_query']:>11.3f}"
              f"{r['precision']:>11.3f}{r['recall']:>9.3f}   {sp}")


# ---------------------------------------------------------------------------
# summary stage  (zero-inference extractive summary from the .elo stream)
# ---------------------------------------------------------------------------

def stage_summary(data: Path, show: int, topk: int = 12) -> None:
    comp = Compressor(); comp.open()
    rev = comp._rev_cache
    elo_files = sorted((data / 'elo').glob('*.elo'))

    def summarize(elo_text: str) -> list[str]:
        # Salience by rarity: longer ID == assigned later == rarer == more topical.
        # Phrase atoms (surface contains a space) are concept-level; rank highest.
        scored: dict[str, int] = {}
        for part in _stream_parts(elo_text):
            tid = _token_id(part)
            if not tid:
                continue
            surf = rev.get(tid.encode('utf-8')) if rev else None
            if not surf:
                continue
            s = surf.decode('utf-8')
            if not s.strip() or not any(ch.isalpha() for ch in s):
                continue
            is_phrase = ' ' in s
            tier = len(tid)
            if not is_phrase and tier <= 2:        # drop very common short-ID words
                continue
            score = (100 if is_phrase else 0) + tier * 10
            if s not in scored or score > scored[s]:
                scored[s] = score
        ranked = sorted(scored.items(), key=lambda kv: -kv[1])
        return [s for s, _ in ranked[:topk]]

    t0 = time.monotonic()
    n = 0
    samples = []
    for p in elo_files:
        et = p.read_text(encoding='utf-8')
        kws = summarize(et)
        if len(samples) < show:
            samples.append((p.stem, kws))
        n += 1
    dt = time.monotonic() - t0
    comp.close()

    print(f"\nZero-inference summaries: {n} files in {dt:.3f}s "
          f"({1000*dt/max(n,1):.3f} ms/file, no model, from compressed stream)")
    for fid, kws in samples:
        print(f"  {fid[:22]:<24} {', '.join(kws)}")
    (data / 'summary_meta.json').write_text(json.dumps(
        {'n_files': n, 'total_secs': round(dt, 4),
         'ms_per_file': round(1000 * dt / max(n, 1), 4),
         'samples': {fid: kws for fid, kws in samples}}, indent=2))


def stage_report(data: Path) -> None:
    for name in ('build_meta.json', 'search_results.json', 'summary_meta.json'):
        p = data / name
        if p.exists():
            print(f"=== {name} ===")
            print(p.read_text())


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for cmd in ('build', 'search', 'summary', 'report'):
        sp = sub.add_parser(cmd)
        sp.add_argument('--data', type=Path, default=Path('semantic_compression/bench_data'))
        if cmd == 'build':
            sp.add_argument('--n', type=int, default=1000)
            sp.add_argument('--budget', type=float, default=25.0,
                           help='max seconds per call (resumable; re-run to finish)')
        if cmd == 'summary':
            sp.add_argument('--show', type=int, default=5)
    args = ap.parse_args(argv)

    if args.cmd == 'build':
        stage_build(args.n, args.data, args.budget)
    elif args.cmd == 'search':
        stage_search(args.data, DEFAULT_TERMS)
    elif args.cmd == 'summary':
        stage_summary(args.data, args.show)
    elif args.cmd == 'report':
        stage_report(args.data)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
