"""
stamp_meta.py -- Stamp / re-stamp dictionary version + status into the `meta`
sub-DB WITHOUT re-faceting.

The content fingerprint is computed over id + surface + facet record only, so
updating meta (release label, status, version, bound_model) does NOT change the
fingerprint. Use this to mark a release as `staged` (unfrozen, more features
pending), `frozen` (immutable contract), or `locked` (frozen AND paired to an
LLM retrain) without rebuilding the facets.

Usage:
    python stamp_meta.py --db db/dictionary.lmdb --release v0.4.0 --status staged
    python stamp_meta.py --release v0.4.0 --status frozen --version 4
    # bind a (frozen) dictionary to an LLM retrain -- protects it from rebuilds:
    python stamp_meta.py --release v0.4.0 --lock-for-model elo-3B-2026-06-20
"""

from __future__ import annotations

import argparse
import struct
from datetime import datetime, timezone
from pathlib import Path

import lmdb

from config import META_DB_NAME

# Allowed lifecycle states for a dictionary release.
STATUS_STAGED = 'staged'   # version in progress; fingerprint NOT a frozen contract
STATUS_FROZEN = 'frozen'   # immutable release; fingerprint IS the contract
STATUS_LOCKED = 'locked'   # frozen AND bound to an LLM retrain (bound_model set);
                           # must not be rebuilt/overwritten -- the model is paired
                           # to this exact fingerprint (see llm-training/RETRAINING.md)
VALID_STATUS = (STATUS_STAGED, STATUS_FROZEN, STATUS_LOCKED)


def stamp(db_path: str, release: str, status: str,
          version: int | None = None, bound_model: str | None = None) -> dict:
    if status not in VALID_STATUS:
        raise ValueError(f'status must be one of {VALID_STATUS}, got {status!r}')
    if status == STATUS_LOCKED and not bound_model:
        raise ValueError("status 'locked' requires bound_model "
                         "(the LLM retrain/model id this dictionary is paired to)")
    if not Path(db_path).exists():
        raise FileNotFoundError(db_path)

    env = lmdb.open(db_path, map_size=2 * 1024 ** 3, max_dbs=4)
    meta_db = env.open_db(META_DB_NAME, create=True)
    with env.begin(write=True) as txn:
        txn.put(b'dictionary_release', release.encode('utf-8'), db=meta_db)
        txn.put(b'dictionary_status', status.encode('utf-8'), db=meta_db)
        if version is not None:
            txn.put(b'dictionary_version', struct.pack('<I', version), db=meta_db)
        if bound_model is not None:
            txn.put(b'bound_model', bound_model.encode('utf-8'), db=meta_db)
            txn.put(b'bound_at', datetime.now(timezone.utc)
                    .isoformat(timespec='seconds').encode('utf-8'), db=meta_db)

    # Read back for confirmation.
    out: dict[str, object] = {}
    int_keys = {b'dictionary_version', b'facets_format_version', b'record_width',
                b'normalization_version', b'dictionary_format_version'}
    with env.begin() as txn:
        for k, v in txn.cursor(db=meta_db):
            out[k.decode()] = struct.unpack('<I', v)[0] if k in int_keys else v.decode()
    env.close()
    return out


def main() -> None:
    p = argparse.ArgumentParser(description='Stamp dictionary release/status into meta')
    p.add_argument('--db', default='db/dictionary.lmdb')
    p.add_argument('--release', required=True, help='e.g. v0.4.0')
    p.add_argument('--status', default=STATUS_STAGED, choices=VALID_STATUS)
    p.add_argument('--version', type=int, default=None,
                   help='optional dictionary_version int')
    p.add_argument('--bound-model', default=None,
                   help='LLM retrain/model id this dictionary is paired to '
                        '(required when --status locked)')
    p.add_argument('--lock-for-model', metavar='MODEL_ID', default=None,
                   help='convenience: set --status locked --bound-model MODEL_ID '
                        '(marks the dictionary as bound to an LLM retrain)')
    args = p.parse_args()
    status = args.status
    bound_model = args.bound_model
    if args.lock_for_model:
        status = STATUS_LOCKED
        bound_model = args.lock_for_model
    meta = stamp(args.db, args.release, status, args.version, bound_model)
    print('meta after stamp:')
    for k, v in meta.items():
        print(f'  {k:26} {v}')


if __name__ == '__main__':
    main()
