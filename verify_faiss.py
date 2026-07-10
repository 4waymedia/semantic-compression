"""
verify_faiss.py -- gate: is the FAISS index still bound to its EPA substrate?

The index is a SECOND ARTIFACT derived from `epa_substrate.lmdb`. Nothing in the
filesystem keeps them aligned. `faiss_query()` returns row indices which are mapped
through `dictionary.faiss.surfaces.json` -- so if the substrate grew, shrank, or was
rebuilt, every neighbour the verbalizer returns is the wrong word. Silently.

This is the two-artifact ID-alignment hazard named in CLAUDE.md. It is now checkable.

Checks
  1. sidecar exists and is faiss_format_version >= 2
     (v1 hashed data.mdb FILE BYTES -- changes on page reorder, not comparable with
      the content fingerprint the rest of the system uses; unverifiable by design)
  2. recorded epa_fingerprint == live content fingerprint of the substrate
  3. surfaces list length == index.ntotal        (skipped if faiss is absent)
  4. surfaces_sha256 matches the shipped surfaces list

Run:
    cd semantic_compression
    python verify_faiss.py
    python verify_faiss.py --epa-db ../Memory/data/epa_substrate.lmdb
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_HERE = Path(__file__).parent
DEFAULT_INDEX = _HERE / 'db' / 'dictionary.faiss.index'
DEFAULT_EPA = _HERE / '..' / 'Memory' / 'data' / 'epa_substrate.lmdb'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--index', type=Path, default=DEFAULT_INDEX)
    ap.add_argument('--epa-db', type=Path, default=DEFAULT_EPA)
    a = ap.parse_args()

    meta_path = a.index.with_suffix('.json')
    fails: list[str] = []

    print(f'index    {a.index}')
    print(f'epa db   {a.epa_db}')
    print()

    if not meta_path.exists():
        print(f'[FAIL] no sidecar {meta_path.name} -- binding unrecorded')
        return 1
    meta = json.loads(meta_path.read_text(encoding='utf-8'))

    # 1. format version
    ver = meta.get('faiss_format_version', 1)
    if ver < 2:
        fails.append('sidecar is v1 (hashed data.mdb bytes; unverifiable). Rebuild.')
        print(f'[FAIL] faiss_format_version = {ver}  (need >= 2)')
    else:
        print(f'[ ok ] faiss_format_version = {ver}')

    # 2. content fingerprint
    try:
        from artifact_identity import _epa_fingerprint
        live = _epa_fingerprint(a.epa_db)
    except Exception as e:                                    # pragma: no cover
        live = None
        print(f'[warn] cannot fingerprint substrate: {e}')
    recorded = meta.get('epa_fingerprint')
    if live is None:
        fails.append('live substrate fingerprint unavailable (lmdb missing?)')
    elif ver >= 2 and live == recorded:
        print(f'[ ok ] epa_fingerprint matches   {live}')
    elif ver >= 2:
        fails.append(f'epa_fingerprint drift: index={recorded} live={live}')
        print(f'[FAIL] epa_fingerprint drift\n         index built against {recorded}\n'
              f'         live substrate is    {live}')

    # 3 + 4. surfaces
    surf_path = Path(meta.get('surfaces_path', ''))
    if not surf_path.is_absolute():
        surf_path = _HERE / surf_path
    if surf_path.exists():
        surfaces = json.loads(surf_path.read_text(encoding='utf-8'))
        print(f'[ ok ] surfaces list           {len(surfaces):,}')
        sha = hashlib.sha256('\n'.join(surfaces).encode('utf-8')).hexdigest()[:16]
        rec_sha = meta.get('surfaces_sha256')
        if rec_sha and sha != rec_sha:
            fails.append(f'surfaces_sha256 drift: {rec_sha} != {sha}')
            print(f'[FAIL] surfaces_sha256 drift   {rec_sha} != {sha}')
        elif rec_sha:
            print(f'[ ok ] surfaces_sha256 matches {sha}')
        try:
            import faiss                                       # optional
            idx = faiss.read_index(str(a.index))
            if idx.ntotal != len(surfaces):
                fails.append(f'index rows {idx.ntotal:,} != surfaces {len(surfaces):,}')
                print(f'[FAIL] index rows {idx.ntotal:,} != surfaces {len(surfaces):,}')
            else:
                print(f'[ ok ] index rows == surfaces  {idx.ntotal:,}  dim={idx.d}')
        except ImportError:
            print('[skip] faiss not installed -- row-count check skipped')
    else:
        fails.append(f'surfaces file missing: {surf_path}')

    print()
    if fails:
        print(f'FAIL ({len(fails)})')
        for f in fails:
            print(f'  - {f}')
        print('\nRebuild:  python faiss_builder.py --epa-db ../Memory/data/epa_substrate.lmdb')
        return 1
    print('ALL CHECKS PASSED -- the index is bound to this substrate')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
