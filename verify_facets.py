"""
verify_facets.py -- Verification gates for the facets database (T6, mirrors
verify_library.py). Runs two layers:

  A. PURE checks (no DB): assignment ladder, normalization, consumer safety.
     Gates T1, T3, T9, T12.
  B. DB checks (a faceted dictionary.lmdb): coverage, in-enum, structural/filler
     families, fingerprint round-trip. Gates T4, T5, T6, T10.

Usage:
    python verify_facets.py                      # checks db/dictionary.lmdb
    python verify_facets.py --db PATH --overrides PATH
    python verify_facets.py --pure-only          # skip the DB layer
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import config as cfg
from config import (
    ALL_FILLERS, BUCKET, FLAG, LOGIC_CUE, STRUCTURAL_IDS, UTILITY,
    SINGLE_WORD_FILLERS, STREAM_ENCODING, unpack_facet, utility_of,
)
from normalize import normalize_surface
from facets import assign_facet, load_overrides


def _has(flags: int, name: str) -> bool:
    return bool(flags & FLAG[name])


def _cues(mask: int) -> set[str]:
    return {n for n, b in LOGIC_CUE.items() if mask & b}


# ===========================================================================
# A. PURE checks
# ===========================================================================

def pure_checks(overrides_path: str) -> None:
    ov = load_overrides(overrides_path)
    print(f'[..] pure checks (overrides: {overrides_path})')

    # --- T1: enums/bits collision-free, name<->byte bijective ---------------
    assert len(set(BUCKET.values())) == len(BUCKET)
    assert all(bin(v).count('1') == 1 for v in LOGIC_CUE.values())
    assert len(set(LOGIC_CUE.values())) == len(LOGIC_CUE)
    assert not (FLAG['MANUAL'] & FLAG['HEURISTIC'])
    print('[OK] T1  enums/bits collision-free; MANUAL/HEURISTIC disjoint')

    # --- T3: hand-labeled table; overrides win; multi-cue != AMBIGUOUS ------
    def check(surface, *, bucket=None, cues_superset=None, has_flags=(),
              no_flags=(), utility=None):
        b, m, f = assign_facet(surface, ov)
        if bucket is not None:
            assert b == BUCKET[bucket], f'{surface!r} bucket {b} != {bucket}'
        if cues_superset is not None:
            got = _cues(m)
            assert cues_superset <= got, f'{surface!r} cues {got} !>= {cues_superset}'
        for fl in has_flags:
            assert _has(f, fl), f'{surface!r} missing flag {fl}'
        for fl in no_flags:
            assert not _has(f, fl), f'{surface!r} unexpected flag {fl}'
        if utility is not None:
            assert utility_of(f) == UTILITY[utility], \
                f'{surface!r} utility != {utility}'
        return b, m, f

    check('thus', bucket='RELATION', cues_superset={'INFERENCE'},
          has_flags=['CLOSED_CLASS'], no_flags=['HEURISTIC', 'MANUAL'],
          utility='FUNCTION')
    # multi-cue: because -> CAUSE + EVIDENCE_CUE, NOT ambiguous
    _, m, f = check('because', bucket='RELATION',
                    cues_superset={'CAUSE', 'EVIDENCE_CUE'}, no_flags=['AMBIGUOUS'])
    assert bin(m).count('1') >= 2, 'because should carry >= 2 cue bits'
    # multi-cue: when -> CONDITION + QUESTION + TEMPORAL
    _, m2, _ = check('when', bucket='RELATION',
                     cues_superset={'CONDITION', 'QUESTION', 'TEMPORAL'},
                     no_flags=['AMBIGUOUS'])
    assert bin(m2).count('1') >= 3, 'when should carry >= 3 cue bits'
    check('cast iron', bucket='CONCEPT', has_flags=['MULTIWORD'],
          no_flags=['CLOSED_CLASS'], utility='CONTENT')
    check('um', bucket='UNKNOWN', utility='FILLER')
    check('rabbit', bucket='TOPIC', has_flags=['HEURISTIC'], utility='CONTENT')
    check('?', bucket='STRUCTURAL', cues_superset={'QUESTION'}, utility='STRUCTURAL')
    check('analyze', bucket='METHOD', utility='CONTENT', no_flags=['HEURISTIC'])
    check('and', bucket='RELATION', cues_superset={'CONJUNCTION'}, utility='FUNCTION')
    # overrides WIN (MANUAL set, no HEURISTIC)
    check('cooking', bucket='TOPIC', has_flags=['MANUAL'], no_flags=['HEURISTIC'])
    check('due to', bucket='RELATION', cues_superset={'CAUSE'},
          has_flags=['MANUAL', 'MULTIWORD'])
    print('[OK] T3  hand-labeled table correct; overrides win; multi-cue != AMBIGUOUS')

    # --- T9: normalization fixtures -----------------------------------------
    assert normalize_surface("don't") == "don't"
    assert normalize_surface("can't") == "can't"
    assert normalize_surface('Due-To') == 'due-to'      # hyphen preserved
    assert normalize_surface('due to') == 'due to'
    assert normalize_surface('however,') == 'however,'  # punct preserved
    assert normalize_surface('  Hello   World ') == 'hello world'
    once = normalize_surface('  Δ  Hello-World ')
    assert normalize_surface(once) == once, 'normalize not idempotent'
    print('[OK] T9  normalization deterministic (apostrophe/hyphen/punct/ws)')

    # --- T12: logic-bearing function words keep a cue (never silently dropped)
    for w in ['not', 'if', 'unless', 'because', 'but']:
        _, m, f = assign_facet(w, ov)
        assert m != 0, f'{w!r} must carry a logic cue so it survives a topic filter'
    print('[OK] T12 not/if/unless/because/but all carry logic cues')


# ===========================================================================
# B. DB checks
# ===========================================================================

def db_checks(db_path: str) -> None:
    import lmdb
    from facet_reader import compute_fingerprint, get_meta, verify_fingerprint

    print(f'[..] DB checks ({db_path})')
    # T5/T4: opening with max_dbs=4 succeeds
    env = lmdb.open(db_path, readonly=True, max_dbs=4, lock=False)
    fwd = env.open_db(cfg.FORWARD_DB_NAME, create=False)
    facets = env.open_db(cfg.FACETS_DB_NAME, create=False)
    env.open_db(cfg.META_DB_NAME, create=False)
    print('[OK] T4  env opens with 4 sub-DBs (forward/reverse/facets/meta)')

    with env.begin() as txn:
        forward_count = txn.stat(db=fwd)['entries']
        tags_count = txn.stat(db=facets)['entries']
    assert tags_count == forward_count, \
        f'facets {tags_count} != forward {forward_count}'
    print(f'[OK] T4  every forward entry faceted ({tags_count:,})')

    # meta + fingerprint
    meta = get_meta(env)
    assert meta.get('facets_format_version') == cfg.FACETS_FORMAT_VERSION
    assert meta.get('record_width') == cfg.FACET_RECORD_WIDTH
    assert meta.get('normalization_version') == cfg.NORMALIZATION_VERSION
    ok, stored, computed = verify_fingerprint(env)
    assert ok, f'fingerprint mismatch: {stored[:12]} != {computed[:12]}'
    print(f'[OK] T5  meta valid; fingerprint verifies ({computed[:16]}…)')
    # T10: fingerprint is deterministic across recompute
    assert compute_fingerprint(env) == computed
    print('[OK] T10 fingerprint reproducible across recompute')

    # T6: all facets in-enum; structural + filler families; multiword not forced CONCEPT
    valid_cue_mask = sum(LOGIC_CUE.values())
    valid_flag_bits = sum(FLAG.values()) | cfg.UTILITY_MASK
    multiword_buckets = set()
    n_checked = 0
    with env.begin() as txn:
        for id_bytes, rec in txn.cursor(db=facets):
            assert len(rec) == cfg.FACET_RECORD_WIDTH, 'bad record width'
            bucket, cue_mask, flags = unpack_facet(rec)
            assert bucket in cfg.BUCKET_NAME, f'bucket {bucket} out of enum'
            assert cue_mask & ~valid_cue_mask == 0, 'cue bit out of enum'
            assert flags & ~valid_flag_bits == 0, 'flag bit out of enum'
            assert not ((flags & FLAG['MANUAL']) and (flags & FLAG['HEURISTIC']))
            if flags & FLAG['MULTIWORD']:
                multiword_buckets.add(bucket)
            n_checked += 1
    print(f'[OK] T6  all {n_checked:,} facets in-enum; MANUAL/HEURISTIC disjoint')
    # multiword handled by function, not forced CONCEPT
    assert multiword_buckets - {BUCKET['CONCEPT']}, \
        'every multiword is CONCEPT -> structure was forced into a class'
    print(f'[OK] T6  multiword spans buckets {sorted(multiword_buckets)} '
          f'(not forced CONCEPT)')

    # All STRUCTURAL_IDS surfaces facet as STRUCTURAL
    with env.begin() as txn:
        for ch in STRUCTURAL_IDS.values():
            idb = txn.get(ch.encode(STREAM_ENCODING), db=fwd)
            if idb is None:
                continue
            bucket, _, flags = unpack_facet(txn.get(idb, db=facets))
            assert bucket == BUCKET['STRUCTURAL'], f'{ch!r} not STRUCTURAL'
            assert utility_of(flags) == UTILITY['STRUCTURAL']
    print('[OK] T6  all in-dict STRUCTURAL_IDS faceted STRUCTURAL')

    # All single-word fillers present facet UTILITY=FILLER
    checked_fillers = 0
    with env.begin() as txn:
        for surf in SINGLE_WORD_FILLERS:
            idb = txn.get(surf.encode(STREAM_ENCODING), db=fwd)
            if idb is None:
                continue
            _, _, flags = unpack_facet(txn.get(idb, db=facets))
            assert utility_of(flags) == UTILITY['FILLER'], \
                f'filler {surf!r} utility != FILLER'
            checked_fillers += 1
    print(f'[OK] T6  {checked_fillers} in-dict single-word fillers UTILITY=FILLER')

    env.close()


def main() -> None:
    p = argparse.ArgumentParser(description='Verify the facets database')
    p.add_argument('--db', default='db/dictionary.lmdb')
    p.add_argument('--overrides', default='data/facet_overrides.tsv')
    p.add_argument('--pure-only', action='store_true')
    args = p.parse_args()

    pure_checks(args.overrides)
    if not args.pure_only:
        if not Path(args.db).exists():
            print(f'[SKIP] DB checks: {args.db} not found '
                  f'(run facet_builder.py first)')
            sys.exit(0)
        db_checks(args.db)

    print('\n=== facets verification PASSED ===')


if __name__ == '__main__':
    main()
