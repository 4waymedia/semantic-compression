"""
facet_builder.py -- Build the facets + meta sub-databases (T4).

Adds the static annotation layer to an EXISTING frozen dictionary.lmdb in
place: raises max_dbs 2 -> 4, writes one 4-byte facet record per `forward`
entry (keyed by the same id_bytes), writes the `meta` sub-DB (versions +
identity + override hash + content fingerprint), and refreshes a human
report (dict_stats_facets.json).

Guarantees:
  - `forward` / `reverse` are READ ONLY here; they stay byte-exact.
  - Re-running is idempotent (facets overwritten by id, fingerprint recomputed).
  - No model inference, no embeddings (assign_facet is pure string logic).

Usage:
    python facet_builder.py                         # facet db/dictionary.lmdb
    python facet_builder.py --db PATH --overrides PATH --dict-id general
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import lmdb

import config as cfg
from config import (
    FORWARD_DB_NAME, META_DB_NAME, REVERSE_DB_NAME, STREAM_ENCODING,
    FACETS_DB_NAME, FLAG, pack_facet,
)
from facets import assign_facet, load_overrides
from facet_reader import (
    bucket_name, compute_fingerprint, cue_names, describe_facet, get_facet,
    utility_name,
)

DEFAULT_DB        = Path('db/dictionary.lmdb')
DEFAULT_OVERRIDES = Path('data/facet_overrides.tsv')
DEFAULT_STATS     = Path('db/dict_stats_facets.json')
MAP_SIZE_BYTES    = 2 * 1024 ** 3   # headroom over the ~1GB general build


def _override_sha256(path: Path) -> str:
    if not path or not Path(path).exists():
        return ''
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_meta(txn, meta_db, identity: dict, override_hash: str) -> None:
    """Write fixed meta keys. Ints are little-endian uint32; rest are utf-8."""
    def put_int(k: str, v: int) -> None:
        txn.put(k.encode('utf-8'), struct.pack('<I', v), db=meta_db)

    def put_str(k: str, v: str) -> None:
        txn.put(k.encode('utf-8'), v.encode('utf-8'), db=meta_db)

    put_int('facets_format_version', cfg.FACETS_FORMAT_VERSION)
    put_int('dictionary_format_version', identity['dictionary_format_version'])
    put_int('record_width', cfg.FACET_RECORD_WIDTH)
    put_int('normalization_version', cfg.NORMALIZATION_VERSION)
    put_str('dictionary_family', identity['dictionary_family'])
    put_str('dictionary_id', identity['dictionary_id'])
    put_int('dictionary_version', identity['dictionary_version'])
    put_str('override_sha256', override_hash)
    put_str('built_at', datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    # dictionary_fingerprint is written in a second pass (depends on facet records).


def build_facets(
    lmdb_path: Path = DEFAULT_DB,
    overrides_path: Path = DEFAULT_OVERRIDES,
    stats_path: Path = DEFAULT_STATS,
    dictionary_family: str = 'general',
    dictionary_id: str = 'general',
    dictionary_version: int = 1,
    dictionary_format_version: int = 3,
    verbose: bool = True,
) -> dict:
    """Facet an existing dictionary.lmdb in place. Returns the stats dict."""
    lmdb_path = Path(lmdb_path)
    if not lmdb_path.exists():
        raise FileNotFoundError(f'dictionary not found: {lmdb_path}')

    overrides = load_overrides(str(overrides_path))
    n_overrides = len(overrides['exact']) + len(overrides['normalized'])
    if verbose:
        print(f'Loaded {n_overrides} overrides from {overrides_path}')

    env = lmdb.open(str(lmdb_path), map_size=MAP_SIZE_BYTES, max_dbs=4)
    fwd_db = env.open_db(FORWARD_DB_NAME, create=False)
    rev_db = env.open_db(REVERSE_DB_NAME, create=False)
    tags_db = env.open_db(FACETS_DB_NAME, create=True)
    meta_db = env.open_db(META_DB_NAME, create=True)

    with env.begin() as txn:
        forward_count = txn.stat(db=fwd_db)['entries']
    if verbose:
        print(f'Tagging {forward_count:,} forward entries...')

    bucket_hist: Counter[str] = Counter()
    cue_hist: Counter[str] = Counter()
    utility_hist: Counter[str] = Counter()
    manual_count = 0
    heuristic_count = 0
    ambiguous_count = 0
    multiword_count = 0
    faceted = 0

    t0 = time.perf_counter()
    # Single write transaction: read each forward (surface -> id), facet, store
    # by id. forward/reverse are not modified.
    with env.begin(write=True) as txn:
        for surface_bytes, id_bytes in txn.cursor(db=fwd_db):
            surface = surface_bytes.decode(STREAM_ENCODING)
            bucket, cue_mask, flags = assign_facet(surface, overrides)

            # Builder invariant: MANUAL and HEURISTIC are never both set.
            if (flags & FLAG['MANUAL']) and (flags & FLAG['HEURISTIC']):
                raise AssertionError(
                    f'MANUAL and HEURISTIC both set for {surface!r}'
                )

            txn.put(id_bytes, pack_facet(bucket, cue_mask, flags), db=tags_db)
            faceted += 1

            bucket_hist[bucket_name(bucket)] += 1
            utility_hist[utility_name(flags)] += 1
            for name in cue_names(cue_mask):
                cue_hist[name] += 1
            if flags & FLAG['MANUAL']:
                manual_count += 1
            if flags & FLAG['HEURISTIC']:
                heuristic_count += 1
            if flags & FLAG['AMBIGUOUS']:
                ambiguous_count += 1
            if flags & FLAG['MULTIWORD']:
                multiword_count += 1

        _write_meta(
            txn, meta_db,
            identity={
                'dictionary_family': dictionary_family,
                'dictionary_id': dictionary_id,
                'dictionary_version': dictionary_version,
                'dictionary_format_version': dictionary_format_version,
            },
            override_hash=_override_sha256(Path(overrides_path)),
        )
    tag_seconds = time.perf_counter() - t0

    # Second pass: fingerprint depends on the now-written facet records.
    fingerprint = compute_fingerprint(env)
    with env.begin(write=True) as txn:
        txn.put(b'dictionary_fingerprint', fingerprint.encode('utf-8'), db=meta_db)

    with env.begin() as txn:
        facets_total = txn.stat(db=tags_db)['entries']

    env.close()

    if faceted != forward_count or facets_total != forward_count:
        raise AssertionError(
            f'facet count mismatch: forward={forward_count} '
            f'faceted={faceted} tags_db={facets_total}'
        )

    stats = {
        'lmdb_path': str(lmdb_path),
        'facets_format_version': cfg.FACETS_FORMAT_VERSION,
        'normalization_version': cfg.NORMALIZATION_VERSION,
        'record_width': cfg.FACET_RECORD_WIDTH,
        'dictionary_family': dictionary_family,
        'dictionary_id': dictionary_id,
        'dictionary_version': dictionary_version,
        'dictionary_fingerprint': fingerprint,
        'forward_count': forward_count,
        'facets_total': facets_total,
        'override_count': n_overrides,
        'manual_count': manual_count,
        'heuristic_count': heuristic_count,
        'ambiguous_count': ambiguous_count,
        'multiword_count': multiword_count,
        'bucket_histogram': dict(bucket_hist.most_common()),
        'cue_histogram': dict(cue_hist.most_common()),
        'utility_histogram': dict(utility_hist.most_common()),
        'tag_seconds': round(tag_seconds, 3),
    }

    Path(stats_path).parent.mkdir(parents=True, exist_ok=True)
    with open(stats_path, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2)

    if verbose:
        _print_stats(stats)
    return stats


def _print_stats(s: dict) -> None:
    print()
    print('=== Facets DB Build Complete ===')
    print(f"  LMDB:                 {s['lmdb_path']}")
    print(f"  Identity:             {s['dictionary_family']}/{s['dictionary_id']} "
          f"v{s['dictionary_version']}")
    print(f"  Fingerprint:          {s['dictionary_fingerprint'][:16]}…")
    print(f"  Faceted entries:       {s['facets_total']:>10,}  "
          f"(forward={s['forward_count']:,})")
    print(f"  Manual / Heuristic:   {s['manual_count']:,} / {s['heuristic_count']:,}")
    print(f"  Ambiguous / Multiword:{s['ambiguous_count']:,} / {s['multiword_count']:,}")
    print(f"  Tagging time:         {s['tag_seconds']}s")
    print('  Bucket histogram:')
    for name, n in s['bucket_histogram'].items():
        print(f'    {name:<12} {n:>10,}')
    print('  Utility histogram:')
    for name, n in s['utility_histogram'].items():
        print(f'    {name:<12} {n:>10,}')
    print('  Top cues:')
    for name, n in list(s['cue_histogram'].items())[:8]:
        print(f'    {name:<14} {n:>10,}')


def spot_check(words: list[str], lmdb_path: Path = DEFAULT_DB) -> None:
    """Print the facet record for each given surface form (max_dbs=4)."""
    env = lmdb.open(str(lmdb_path), readonly=True, max_dbs=4, lock=False)
    fwd_db = env.open_db(FORWARD_DB_NAME, create=False)
    print(f"\n{'SURFACE':<22}{'ID':<8}{'BUCKET':<11}{'UTIL':<11}{'CUES / FLAGS'}")
    print('-' * 78)
    with env.begin() as txn:
        for w in words:
            id_bytes = txn.get(w.encode(STREAM_ENCODING), db=fwd_db)
            if id_bytes is None:
                print(f"{w:<22}{'(OOV)':<8}")
                continue
            facet = get_facet(env, id_bytes)
            d = describe_facet(facet)
            cf = ','.join(d['cues']) or '-'
            ff = ','.join(d['flags']) or '-'
            print(f"{w:<22}{id_bytes.decode():<8}{d['bucket']:<11}"
                  f"{d['utility']:<11}{cf}  [{ff}]")
    env.close()


def main() -> None:
    p = argparse.ArgumentParser(description='Build facets + meta sub-DBs in place')
    p.add_argument('--db', default=str(DEFAULT_DB))
    p.add_argument('--overrides', default=str(DEFAULT_OVERRIDES))
    p.add_argument('--stats', default=str(DEFAULT_STATS))
    p.add_argument('--dict-family', default='general')
    p.add_argument('--dict-id', default='general')
    p.add_argument('--dict-version', type=int, default=1)
    args = p.parse_args()
    build_facets(
        lmdb_path=Path(args.db),
        overrides_path=Path(args.overrides),
        stats_path=Path(args.stats),
        dictionary_family=args.dict_family,
        dictionary_id=args.dict_id,
        dictionary_version=args.dict_version,
    )
    spot_check(
        ['therefore', 'because', 'cast iron', 'um', 'rabbit', '?', 'the',
         'cooking', 'due to', 'when', 'and', 'analyze'],
        lmdb_path=Path(args.db),
    )


if __name__ == '__main__':
    main()
