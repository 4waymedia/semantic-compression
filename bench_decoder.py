"""
bench_decoder.py — before/after benchmark for decode-spec-v01.

Spec item #7 deliverable. Reports per spec:
    decode_MBps
    encode_MBps
    compression_ratio
    round_trip   (PASS / FAIL)
    init_ms

Plus useful side metrics:
    rev_cache_bytes / fwd_cache_bytes  — resident size of each cache
    encode_tokens_per_sec               — sanity metric
    speedup_vs_uncached                 — direct comparison ratio

Two configurations are benchmarked head-to-head on the same inputs:
    UNCACHED   — preload_rev_cache=False, preload_fwd_cache=False  (baseline)
    CACHED     — preload_rev_cache=True,  preload_fwd_cache=True   (this PR)

Run from project root:
    python -m semantic_compression.bench_decoder
    python -m semantic_compression.bench_decoder --samples 1 --transcript jocko
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, '.')

from semantic_compression.compressor import Compressor


# ---------------------------------------------------------------------------
# Sample selection
# ---------------------------------------------------------------------------

V1_SAMPLES_DIR = Path('semantic_compression/samples')

V1_SAMPLE_FILES = [
    'plain.txt',
    'readme.md',
    'config.json',
    'contacts.csv',
    'feed.xml',
    'page.html',
    'service.yaml',
    'server.log',
    'episode.srt',
    'episode.vtt',
]

# Real transcripts (large inputs — the meaningful throughput signal)
TRANSCRIPT_FILES = {
    'times_now': 'Resources/transcripts/times_now/KhyQCU6oqE8.json',
    'jocko':     'Resources/transcripts/jocko_podcast/KOhbGjmidgs.json',
    'julian':    'Resources/transcripts/julian_dorey/bRwFb8JmznE.json',
}


# ---------------------------------------------------------------------------
# Bench primitives
# ---------------------------------------------------------------------------

def _read_file(path: Path) -> Optional[bytes]:
    if not path.exists():
        return None
    return path.read_bytes()


def _now() -> float:
    return time.perf_counter()


def _mbps(byte_count: int, elapsed_s: float) -> float:
    if elapsed_s <= 0:
        return 0.0
    return (byte_count / elapsed_s) / 1_000_000.0


def run_one_payload(c: Compressor, src: bytes, fmt: str) -> dict:
    """
    Encode + decode + round-trip check for a single payload.
    Returns a dict of metrics.
    """
    # Encode
    t0 = _now()
    encoded = c.encode_bytes_binary(src, fmt=fmt)
    encode_s = _now() - t0
    # Decode
    t0 = _now()
    ext, decoded = c.decode_bytes_binary(encoded)
    decode_s = _now() - t0

    return {
        'src_bytes':         len(src),
        'encoded_bytes':     len(encoded),
        'encode_seconds':    encode_s,
        'decode_seconds':    decode_s,
        'compression_ratio': len(src) / max(len(encoded), 1),
        'round_trip':        decoded == src,
        'encode_MBps':       _mbps(len(src), encode_s),
        'decode_MBps':       _mbps(len(src), decode_s),
    }


def bench_compressor(
    label: str,
    preload_rev_cache: bool,
    preload_fwd_cache: bool,
    payloads: list[tuple[str, str, bytes]],   # (name, ext, src)
    warmup_payload: Optional[bytes] = None,
    preload_int_cache: bool = False,
) -> dict:
    """
    Open a Compressor with the given cache settings, run all payloads,
    return aggregated metrics. Times init separately.
    """
    print(f'\n--- {label} '
          f'(preload_rev_cache={preload_rev_cache}, '
          f'preload_fwd_cache={preload_fwd_cache}, '
          f'preload_int_cache={preload_int_cache}) ---')

    gc.collect()

    c = Compressor(
        preload_rev_cache=preload_rev_cache,
        preload_fwd_cache=preload_fwd_cache,
        preload_int_cache=preload_int_cache,
    )
    # init_ms is captured automatically inside Compressor.open()
    c.open()

    metrics = {
        'label':            label,
        'init_ms':          c.init_ms,
        'rev_cache_bytes':  c.rev_cache_bytes,
        'fwd_cache_bytes':  c.fwd_cache_bytes,
        'id_cache_bytes':   c.id_cache_bytes,
        'per_payload':      [],
        'totals': {
            'src_bytes':      0,
            'encoded_bytes':  0,
            'encode_seconds': 0.0,
            'decode_seconds': 0.0,
            'round_trip_pass': True,
        },
    }
    print(f'  init_ms:          {c.init_ms:>8.1f}')
    print(f'  rev_cache_bytes:  {c.rev_cache_bytes:>10,}')
    print(f'  fwd_cache_bytes:  {c.fwd_cache_bytes:>10,}')
    print(f'  id_cache_bytes:   {c.id_cache_bytes:>10,}')

    # Warmup: encode/decode a small payload to pay any JIT/cache costs
    # that aren't fundamental to the per-payload work.
    if warmup_payload is not None:
        _ = c.decode_bytes_binary(c.encode_bytes_binary(warmup_payload, fmt='.txt'))

    print(f'  {"payload":<14} {"src_MB":>8} {"enc_MB":>8} {"ratio":>6} '
          f'{"enc_MBps":>10} {"dec_MBps":>10} {"rt":>4}')
    print(f'  {"-"*14} {"-"*8} {"-"*8} {"-"*6} {"-"*10} {"-"*10} {"-"*4}')

    for name, ext, src in payloads:
        m = run_one_payload(c, src, ext)
        metrics['per_payload'].append({'name': name, **m})
        T = metrics['totals']
        T['src_bytes']      += m['src_bytes']
        T['encoded_bytes']  += m['encoded_bytes']
        T['encode_seconds'] += m['encode_seconds']
        T['decode_seconds'] += m['decode_seconds']
        T['round_trip_pass'] = T['round_trip_pass'] and m['round_trip']
        print(
            f'  {name:<14} '
            f'{m["src_bytes"]/1e6:>8.3f} '
            f'{m["encoded_bytes"]/1e6:>8.3f} '
            f'{m["compression_ratio"]:>6.2f} '
            f'{m["encode_MBps"]:>10.2f} '
            f'{m["decode_MBps"]:>10.2f} '
            f'{"PASS" if m["round_trip"] else "FAIL":>4}'
        )

    T = metrics['totals']
    overall_encode_MBps = _mbps(T['src_bytes'], T['encode_seconds'])
    overall_decode_MBps = _mbps(T['src_bytes'], T['decode_seconds'])
    overall_ratio       = T['src_bytes'] / max(T['encoded_bytes'], 1)
    metrics['summary'] = {
        'encode_MBps':       overall_encode_MBps,
        'decode_MBps':       overall_decode_MBps,
        'compression_ratio': overall_ratio,
        'round_trip':        'PASS' if T['round_trip_pass'] else 'FAIL',
        'total_src_MB':      T['src_bytes'] / 1e6,
        'total_encoded_MB':  T['encoded_bytes'] / 1e6,
    }

    print(f'  {"-"*14} {"-"*8} {"-"*8} {"-"*6} {"-"*10} {"-"*10} {"-"*4}')
    print(f'  TOTAL          '
          f'{T["src_bytes"]/1e6:>8.3f} '
          f'{T["encoded_bytes"]/1e6:>8.3f} '
          f'{overall_ratio:>6.2f} '
          f'{overall_encode_MBps:>10.2f} '
          f'{overall_decode_MBps:>10.2f} '
          f'{"PASS" if T["round_trip_pass"] else "FAIL":>4}')

    c.close()
    return metrics


# ---------------------------------------------------------------------------
# Comparison + reporting
# ---------------------------------------------------------------------------

def print_comparison(*configs: dict) -> None:
    """Print a side-by-side table of N configurations."""
    def pct_delta(after: float, before: float) -> str:
        if before == 0:
            return 'n/a'
        d = (after - before) / before * 100
        sign = '+' if d >= 0 else ''
        return f'{sign}{d:.1f}%'

    labels   = [c['label'].split()[0] for c in configs]
    summary  = [c['summary'] for c in configs]
    init_ms  = [c['init_ms']  for c in configs]
    rev_b    = [c['rev_cache_bytes'] for c in configs]
    fwd_b    = [c['fwd_cache_bytes'] for c in configs]
    id_b     = [c.get('id_cache_bytes', 0) for c in configs]

    print()
    print('=' * 78)
    print('BEFORE / AFTER SUMMARY')
    print('=' * 78)
    header = f'  {"metric":<22}' + ''.join(f'{lbl:>14}' for lbl in labels)
    print(header)
    print(f'  {"-"*22}' + ''.join(' ' + '-' * 13 for _ in labels))

    def row(name: str, values: list, fmt: str = '{:>14.2f}') -> str:
        return f'  {name:<22}' + ''.join(fmt.format(v) for v in values)

    print(row('decode_MBps',      [s['decode_MBps']      for s in summary]))
    print(row('encode_MBps',      [s['encode_MBps']      for s in summary]))
    print(row('compression_ratio',[s['compression_ratio'] for s in summary],
              fmt='{:>14.3f}'))
    rt_strs = [s['round_trip'] for s in summary]
    print(f'  {"round_trip":<22}' + ''.join(f'{v:>14}' for v in rt_strs))
    print(row('init_ms',          init_ms,    fmt='{:>14.1f}'))
    print(f'  {"rev_cache_bytes":<22}' + ''.join(f'{v:>14,}' for v in rev_b))
    print(f'  {"fwd_cache_bytes":<22}' + ''.join(f'{v:>14,}' for v in fwd_b))
    print(f'  {"id_cache_bytes":<22}' + ''.join(f'{v:>14,}' for v in id_b))

    # Speedup ladder vs the leftmost (assumed baseline)
    base = summary[0]
    print()
    for c, s in zip(configs[1:], summary[1:]):
        lbl = c['label'].split()[0]
        sd = s['decode_MBps'] / max(base['decode_MBps'], 1e-9)
        se = s['encode_MBps'] / max(base['encode_MBps'], 1e-9)
        dd = pct_delta(s['decode_MBps'], base['decode_MBps'])
        de = pct_delta(s['encode_MBps'], base['encode_MBps'])
        print(f'  {lbl:<22}  decode {sd:.2f}x ({dd})   encode {se:.2f}x ({de})')


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

def gather_payloads(args: argparse.Namespace) -> list[tuple[str, str, bytes]]:
    """Return (name, source_ext, src_bytes) tuples per CLI args."""
    payloads: list[tuple[str, str, bytes]] = []

    if not args.no_v1_samples:
        for fname in V1_SAMPLE_FILES:
            path = V1_SAMPLES_DIR / fname
            data = _read_file(path)
            if data is not None:
                ext = Path(fname).suffix or '.txt'
                payloads.append((fname, ext, data))

    if args.transcript:
        keys = [k.strip() for k in args.transcript.split(',') if k.strip()]
    else:
        keys = list(TRANSCRIPT_FILES.keys())

    for key in keys:
        if key not in TRANSCRIPT_FILES:
            continue
        path = Path(TRANSCRIPT_FILES[key])
        data = _read_file(path)
        if data is not None:
            payloads.append((key, '.json', data))

    return payloads


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            'Before/after benchmark for the decode/encode cache optimization '
            '(decode-spec-v01).'
        ),
    )
    parser.add_argument('--samples', type=int, default=1,
                        help='Re-run all payloads N times (default 1). Useful '
                             'for stable throughput numbers.')
    parser.add_argument('--no-v1-samples', action='store_true',
                        help='Skip the v1 format samples; use only transcripts.')
    parser.add_argument('--transcript', default=None,
                        help='Comma-separated transcript keys to include '
                             f'({"/".join(TRANSCRIPT_FILES.keys())}). '
                             'Default: all available.')
    parser.add_argument('--out-json', default=None,
                        help='Write the full metrics report to this JSON file.')
    args = parser.parse_args(argv)

    payloads = gather_payloads(args)
    if not payloads:
        print('No usable payloads found. Check sample paths and transcript paths.')
        return 1

    # Duplicate the payload list per --samples (for stable timing)
    repeated_payloads = []
    for run_idx in range(args.samples):
        for name, ext, src in payloads:
            repeated_payloads.append(
                (f'{name}#{run_idx}' if args.samples > 1 else name, ext, src)
            )

    # Warmup target: smallest payload, to absorb first-call overhead
    warmup = min(payloads, key=lambda p: len(p[2]))[2]

    # --- Run UNCACHED (LMDB per token) ---
    uncached_metrics = bench_compressor(
        label='UNCACHED   (preload off — txn.get per token)',
        preload_rev_cache=False,
        preload_fwd_cache=False,
        preload_int_cache=False,
        payloads=repeated_payloads,
        warmup_payload=warmup,
    )

    # --- Run BYTES-CACHED (decode-spec-v01) ---
    bytes_cached_metrics = bench_compressor(
        label='BYTES-CACHE  (decode-spec-v01 — dict[bytes,bytes].get)',
        preload_rev_cache=True,
        preload_fwd_cache=True,
        preload_int_cache=False,
        payloads=repeated_payloads,
        warmup_payload=warmup,
    )

    # --- Run INT-CACHED (decode-spec-v02) ---
    int_cached_metrics = bench_compressor(
        label='INT-CACHE   (decode-spec-v02 — dict[int,bytes][packed])',
        preload_rev_cache=True,
        preload_fwd_cache=True,
        preload_int_cache=True,
        payloads=repeated_payloads,
        warmup_payload=warmup,
    )

    print_comparison(uncached_metrics, bytes_cached_metrics, int_cached_metrics)

    if args.out_json:
        out_path = Path(args.out_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(
                {
                    'uncached':     uncached_metrics,
                    'bytes_cache':  bytes_cached_metrics,
                    'int_cache':    int_cached_metrics,
                },
                f, indent=2,
            )
        print(f'\nFull metrics written to: {out_path}')

    return 0 if int_cached_metrics['summary']['round_trip'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
