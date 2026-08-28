"""
facet_reader.py -- Stateless reader API for the facets + meta sub-databases (T5).

A consumer reads a facet the same way it reads `reverse`: an indexed
txn.get(id_bytes, db=facets) with no model inference. All functions are pure
with respect to the LMDB env handed in; they open named sub-DBs read-only.

    get_facet(env, id_bytes)        -> (bucket, cue_mask, flags) | None
    get_meta(env)                 -> {key: value}  (ints decoded, rest utf-8)
    verify_fingerprint(env)       -> (ok, stored_hex, computed_hex)
    compute_fingerprint(env)      -> hex str   (layout-independent content hash)

Decoders (bytes -> names) so nothing opaque reaches a UI/log:
    bucket_name / cue_names / flag_names / utility_name / describe_facet
"""

from __future__ import annotations

import hashlib
import struct

from config import (
    BUCKET_NAME, FLAG, FLAG_NAME, LOGIC_CUE_NAME, META_DB_NAME, REVERSE_DB_NAME,
    FACETS_DB_NAME, UTILITY_NAME, unpack_facet, utility_of,
)

__all__ = [
    'get_facet', 'get_meta', 'compute_fingerprint', 'verify_fingerprint',
    'bucket_name', 'cue_names', 'flag_names', 'utility_name', 'describe_facet',
]

# Meta keys stored as little-endian uint32 (everything else is utf-8 text).
_META_INT_KEYS = frozenset({
    b'facets_format_version', b'dictionary_format_version', b'record_width',
    b'normalization_version', b'dictionary_version',
})


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def get_facet(env, id_bytes: bytes) -> tuple[int, int, int] | None:
    """Return (bucket, cue_mask, flags) for an ID, or None if untagged."""
    tags_db = env.open_db(FACETS_DB_NAME, create=False)
    with env.begin() as txn:
        raw = txn.get(id_bytes, db=tags_db)
    if raw is None:
        return None
    return unpack_facet(raw)


def get_meta(env) -> dict:
    """Return the meta sub-DB as a dict (int keys decoded, others utf-8)."""
    meta_db = env.open_db(META_DB_NAME, create=False)
    out: dict[str, object] = {}
    with env.begin() as txn:
        for k, v in txn.cursor(db=meta_db):
            if k in _META_INT_KEYS:
                out[k.decode('utf-8')] = struct.unpack('<I', v)[0]
            else:
                out[k.decode('utf-8')] = v.decode('utf-8')
    return out


# ---------------------------------------------------------------------------
# Fingerprint (must match the builder exactly)
# ---------------------------------------------------------------------------

def compute_fingerprint(env) -> str:
    """
    Deterministic, layout-independent content fingerprint:

        SHA-256 over, for each entry sorted by id_bytes:
            uint32(len id_bytes) || id_bytes ||
            uint32(len surface_bytes) || surface_bytes ||
            tag_record(4 bytes)

    Source of truth is `reverse` (id_bytes -> byte-exact surface); LMDB cursors
    iterate keys in sorted byte order, which is exactly the required order.
    """
    rev_db = env.open_db(REVERSE_DB_NAME, create=False)
    tags_db = env.open_db(FACETS_DB_NAME, create=False)
    h = hashlib.sha256()
    with env.begin() as txn:
        for id_bytes, surface_bytes in txn.cursor(db=rev_db):
            facet = txn.get(id_bytes, db=tags_db)
            if facet is None:
                facet = b'\x00\x00\x00\x00'
            h.update(struct.pack('<I', len(id_bytes)))
            h.update(id_bytes)
            h.update(struct.pack('<I', len(surface_bytes)))
            h.update(surface_bytes)
            h.update(facet)
    return h.hexdigest()


def verify_fingerprint(env) -> tuple[bool, str, str]:
    """Return (matches, stored_hex, computed_hex).

    compute_fingerprint() hashes id + surface + facet record -- the FACETS-inclusive
    content hash. Since 2026-08-10 that value is stamped under b'facets_fingerprint'
    (b'dictionary_fingerprint' now holds the pure (surface,id) hash, stable across
    facet re-derivation). Compare against the honest key; fall back to the legacy
    key for builds stamped before the split."""
    meta = get_meta(env)
    stored = meta.get('facets_fingerprint') or meta.get('dictionary_fingerprint', '')
    computed = compute_fingerprint(env)
    return (stored == computed, stored, computed)


# ---------------------------------------------------------------------------
# Decoders (no opaque bytes leave this layer)
# ---------------------------------------------------------------------------

def bucket_name(bucket: int) -> str:
    """Unknown values map to UNKNOWN per reader policy (never raise)."""
    return BUCKET_NAME.get(bucket, 'UNKNOWN')


def cue_names(cue_mask: int) -> list[str]:
    """Return cue names set in the mask (empty list == NEUTRAL)."""
    return [name for bit, name in sorted(LOGIC_CUE_NAME.items()) if cue_mask & bit]


def flag_names(flags: int) -> list[str]:
    """Return property/provenance flag names set (excludes the UTILITY bits)."""
    return [name for bit, name in sorted(FLAG_NAME.items()) if flags & bit]


def utility_name(flags: int) -> str:
    return UTILITY_NAME.get(utility_of(flags), 'CONTENT')


def describe_facet(facet: tuple[int, int, int]) -> dict:
    """Verbalize a (bucket, cue_mask, flags) tuple into names."""
    bucket, cue_mask, flags = facet
    return {
        'bucket': bucket_name(bucket),
        'cues': cue_names(cue_mask),
        'flags': flag_names(flags),
        'utility': utility_name(flags),
    }
