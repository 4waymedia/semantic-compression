"""
test_facets.py -- Unit tests for the v0.4.0 semantic facets layer.

Complements verify_facets.py (which gates the full production dictionary). This
file is self-contained: it builds a tiny throwaway LMDB in a temp dir, so it
runs fast and touches no production data. Covers the things the gate suite does
not exercise directly:

    - normalize_surface fixtures + idempotency
    - pack/unpack + utility bit helpers (round-trip, byte-exact)
    - assign_facet hand-labeled table, overrides win, multi-cue, RELATION->FUNCTION
    - load_overrides error handling (unknown bucket/cue, bad mode, version skew)
    - the SHIPPED data/facet_overrides.tsv loads clean
    - reader round-trip on an isolated faceted LMDB (get_facet/get_meta/fingerprint)
    - T11 corruption: tampered facet record + fingerprint mismatch are detectable
    - additive guarantee: faceting leaves forward/reverse byte-exact
    - stamp_meta flips release/status without changing the fingerprint

Run:
    python test_facets.py                       # from the semantic_compression dir
    python -m semantic_compression.test_facets  # from the project root
Also collectable by pytest (plain assert-based test_* functions).
"""

from __future__ import annotations

import hashlib
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lmdb

import config as cfg
from config import BUCKET, FLAG, LOGIC_CUE, UTILITY, pack_facet, unpack_facet, utility_of, set_utility
from normalize import normalize_surface
from facets import assign_facet, load_overrides, OverrideError
import facet_builder
from facet_reader import (
    get_facet, get_meta, verify_fingerprint, compute_fingerprint, describe_facet,
)
import stamp_meta

_OVERRIDES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'data', 'facet_overrides.tsv')


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _cues(mask: int) -> set[str]:
    return {n for n, b in LOGIC_CUE.items() if mask & b}


def _build_tiny_lmdb(entries: dict[str, str]) -> str:
    """Create a temp forward/reverse LMDB (surface->id, id->surface)."""
    d = tempfile.mkdtemp(prefix='sctags_')
    path = os.path.join(d, 'dictionary.lmdb')
    env = lmdb.open(path, map_size=64 * 1024 ** 2, max_dbs=4)
    fwd = env.open_db(cfg.FORWARD_DB_NAME, create=True)
    rev = env.open_db(cfg.REVERSE_DB_NAME, create=True)
    with env.begin(write=True) as txn:
        for surface, sid in entries.items():
            txn.put(surface.encode('utf-8'), sid.encode('utf-8'), db=fwd)
            txn.put(sid.encode('utf-8'), surface.encode('utf-8'), db=rev)
    env.close()
    return path


def _fwdrev_hash(path: str) -> str:
    env = lmdb.open(path, readonly=True, max_dbs=4, lock=False)
    fwd = env.open_db(cfg.FORWARD_DB_NAME, create=False)
    rev = env.open_db(cfg.REVERSE_DB_NAME, create=False)
    h = hashlib.sha256()
    with env.begin() as txn:
        for k, v in txn.cursor(db=fwd):
            h.update(b'F'); h.update(k); h.update(b'='); h.update(v)
        for k, v in txn.cursor(db=rev):
            h.update(b'R'); h.update(k); h.update(b'='); h.update(v)
    env.close()
    return h.hexdigest()


# ---------------------------------------------------------------------------
# pure-function tests
# ---------------------------------------------------------------------------

def test_normalize():
    assert normalize_surface("Don't") == "don't"          # apostrophe + case
    assert normalize_surface('Due-To') == 'due-to'        # hyphen preserved
    assert normalize_surface('however,') == 'however,'    # punct preserved
    assert normalize_surface('  A   B ') == 'a b'         # ws collapse + strip
    s = normalize_surface('  Δ  Mixed-Case ')
    assert normalize_surface(s) == s                      # idempotent


def test_pack_unpack():
    for bucket in BUCKET.values():
        for cue in (0x0000, 0x0012, 0xFFFF & ~0x8000):
            for flags in (0x00, 0x4A, 0xC0, 0xFF):
                rec = pack_facet(bucket, cue, flags)
                assert len(rec) == cfg.FACET_RECORD_WIDTH
                assert unpack_facet(rec) == (bucket, cue, flags)
    # utility helpers round-trip
    f = set_utility(FLAG['CLOSED_CLASS'], UTILITY['FUNCTION'])
    assert utility_of(f) == UTILITY['FUNCTION']
    assert f & FLAG['CLOSED_CLASS']


def test_assign_table():
    ov = load_overrides(_OVERRIDES)

    def b(s):  # bucket name of surface
        return cfg.BUCKET_NAME[assign_facet(s, ov)[0]]

    assert b('thus') == 'RELATION'
    assert b('rabbit') == 'TOPIC'
    assert b('cast iron') == 'CONCEPT'
    assert b('um') == 'UNKNOWN'
    assert b('?') == 'STRUCTURAL'
    assert b('analyze') == 'METHOD'

    # multi-cue is composition, not ambiguity
    _, m, f = assign_facet('because', ov)
    assert {'CAUSE', 'EVIDENCE_CUE'} <= _cues(m)
    assert not (f & FLAG['AMBIGUOUS'])
    _, m2, _ = assign_facet('when', ov)
    assert {'CONDITION', 'QUESTION', 'TEMPORAL'} <= _cues(m2)

    # override wins (MANUAL, no HEURISTIC) and a RELATION is FUNCTION utility
    _, _, fc = assign_facet('cooking', ov)
    assert (fc & FLAG['MANUAL']) and not (fc & FLAG['HEURISTIC'])
    _, _, fd = assign_facet('due to', ov)
    assert utility_of(fd) == UTILITY['FUNCTION']
    assert (fd & FLAG['MANUAL']) and (fd & FLAG['MULTIWORD'])

    # MANUAL and HEURISTIC are never both set
    for surf in ['the', 'and', 'not', 'because', 'cooking', 'rabbit', 'um', '?']:
        _, _, fl = assign_facet(surf, ov)
        assert not ((fl & FLAG['MANUAL']) and (fl & FLAG['HEURISTIC']))


def test_consumer_safety():
    ov = load_overrides(_OVERRIDES)
    # logic-bearing function words must keep a cue so a topic filter never drops them
    for w in ['not', 'if', 'unless', 'because', 'but']:
        _, m, _ = assign_facet(w, ov)
        assert m != 0, w


def test_shipped_overrides_load():
    ov = load_overrides(_OVERRIDES)
    assert (len(ov['exact']) + len(ov['normalized'])) > 0


def test_override_errors():
    d = tempfile.mkdtemp(prefix='sctags_ov_')

    def write(name, body):
        p = os.path.join(d, name)
        Path(p).write_text(body, encoding='utf-8')
        return p

    # unknown bucket
    try:
        load_overrides(write('a.tsv', 'exact:foo\tNOTABUCKET\t-\n')); assert False
    except OverrideError:
        pass
    # unknown cue
    try:
        load_overrides(write('b.tsv', 'exact:foo\tTOPIC\tNOTACUE\n')); assert False
    except OverrideError:
        pass
    # bad mode
    try:
        load_overrides(write('c.tsv', 'bogus:foo\tTOPIC\t-\n')); assert False
    except OverrideError:
        pass
    # version skew in header comment
    try:
        load_overrides(write('d.tsv', '# facets_format_version=999\nexact:foo\tTOPIC\t-\n'))
        assert False
    except OverrideError:
        pass
    # missing file -> empty maps, no error
    empty = load_overrides(os.path.join(d, 'nope.tsv'))
    assert empty == {'exact': {}, 'normalized': {}}


# ---------------------------------------------------------------------------
# build + reader tests (isolated tiny LMDB)
# ---------------------------------------------------------------------------

_ENTRIES = {
    'because': 'ga', 'rabbit': 'jDu', '?': 'o', 'um': 'gT',
    'cast iron': 'hXX', 'and': 'N', 'cooking': 'jMP', 'due to': 'hqN',
}


def _build_tagged() -> str:
    path = _build_tiny_lmdb(_ENTRIES)
    before = _fwdrev_hash(path)
    stats = facet_builder.build_facets(
        lmdb_path=Path(path), overrides_path=Path(_OVERRIDES),
        stats_path=Path(os.path.join(os.path.dirname(path), 'stats.json')),
        verbose=False,
    )
    assert stats['facets_total'] == len(_ENTRIES)
    # additive guarantee: forward/reverse untouched
    assert _fwdrev_hash(path) == before, 'faceting changed forward/reverse!'
    return path


def test_reader_roundtrip():
    path = _build_tagged()
    env = lmdb.open(path, readonly=True, max_dbs=4, lock=False)
    fwd = env.open_db(cfg.FORWARD_DB_NAME, create=False)
    with env.begin() as txn:
        idb = txn.get(b'because', db=fwd)
    facet = get_facet(env, idb)
    d = describe_facet(facet)
    assert d['bucket'] == 'RELATION'
    assert 'CAUSE' in d['cues']
    # unknown id -> None
    assert get_facet(env, b'zzzz') is None
    # meta present + fingerprint verifies
    meta = get_meta(env)
    assert meta['facets_format_version'] == cfg.FACETS_FORMAT_VERSION
    assert meta['record_width'] == cfg.FACET_RECORD_WIDTH
    ok, stored, computed = verify_fingerprint(env)
    assert ok and stored == computed
    env.close()


def test_reproducible_fingerprint():
    p1 = _build_tagged()
    p2 = _build_tagged()
    e1 = lmdb.open(p1, readonly=True, max_dbs=4, lock=False)
    e2 = lmdb.open(p2, readonly=True, max_dbs=4, lock=False)
    assert compute_fingerprint(e1) == compute_fingerprint(e2)
    e1.close(); e2.close()


def test_corruption_detected():
    """T11: a tampered facet record makes the stored fingerprint stop matching."""
    path = _build_tagged()
    env = lmdb.open(path, max_dbs=4)
    tags_db = env.open_db(cfg.FACETS_DB_NAME, create=False)
    # flip one facet record to a different (still valid-width) value
    with env.begin(write=True) as txn:
        idb = b'ga'  # 'because'
        orig = txn.get(idb, db=tags_db)
        tampered = pack_facet(BUCKET['TOPIC'], 0, FLAG['HEURISTIC'])
        assert tampered != orig
        txn.put(idb, tampered, db=tags_db)
    ok, stored, computed = verify_fingerprint(env)
    assert not ok, 'fingerprint should NOT match after tampering'
    env.close()


def test_stamp_meta_keeps_fingerprint():
    path = _build_tagged()
    env = lmdb.open(path, readonly=True, max_dbs=4, lock=False)
    before = compute_fingerprint(env)
    env.close()
    meta = stamp_meta.stamp(path, release='v0.4.0', status='staged', version=4)
    assert meta['dictionary_release'] == 'v0.4.0'
    assert meta['dictionary_status'] == 'staged'
    env = lmdb.open(path, readonly=True, max_dbs=4, lock=False)
    assert compute_fingerprint(env) == before, 'meta stamp must not change fingerprint'
    ok, _, _ = verify_fingerprint(env)
    env.close()
    # fingerprint stored at build time was over facets only -> still valid
    assert ok


# ---------------------------------------------------------------------------
# runner (mirrors verify_*.py style)
# ---------------------------------------------------------------------------

def main() -> None:
    tests = [
        test_normalize, test_pack_unpack, test_assign_table, test_consumer_safety,
        test_shipped_overrides_load, test_override_errors, test_reader_roundtrip,
        test_reproducible_fingerprint, test_corruption_detected,
        test_stamp_meta_keeps_fingerprint,
    ]
    for t in tests:
        t()
        print(f'[OK] {t.__name__}')
    print(f'\n=== test_facets: {len(tests)}/{len(tests)} PASSED ===')


if __name__ == '__main__':
    main()
