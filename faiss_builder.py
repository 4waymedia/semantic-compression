"""
faiss_builder.py
EloAI — EPA Vector Index

Post-build step: reads all (E, P, A) vectors from epa_substrate.lmdb,
builds a FAISS flat L2 index over them, and saves as db/dictionary.faiss.index.

The index is a derived artifact of the dictionary EPA substrate.
It must be rebuilt whenever epa_substrate.lmdb changes.

A companion metadata file db/dictionary.faiss.json records:
  - epa_substrate path + fingerprint
  - entry count + vector dimension
  - build timestamp
  - surface list (parallel to FAISS internal IDs)

Query pattern (verbalizer):
    D, I = index.search(query_vec, k=30)
    surface = id_to_surface[I[0][n]]
    epa     = epa_lookup[surface]

Usage:
    python faiss_builder.py
    python faiss_builder.py --epa-db PATH --out-dir PATH
    python faiss_builder.py --dry-run    # validate, no write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import time
from pathlib import Path

import faiss
import lmdb
import numpy as np

DEFAULT_EPA_DB = Path('../Memory/data/epa_substrate.lmdb')
DEFAULT_OUT    = Path('db')
INDEX_NAME     = 'dictionary.faiss.index'
META_NAME      = 'dictionary.faiss.json'
_EPA_STRUCT    = struct.Struct('<fff')
DIM = 3  # E, P, A


def _fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    data_file = path / 'data.mdb'
    if data_file.exists():
        with open(data_file, 'rb') as f:
            while chunk := f.read(65536):
                h.update(chunk)
    return h.hexdigest()[:16]


def build_faiss_index(
    epa_lmdb_path: Path = DEFAULT_EPA_DB,
    out_dir: Path = DEFAULT_OUT,
    dry_run: bool = False,
) -> dict:
    epa_lmdb_path = Path(epa_lmdb_path)
    out_dir = Path(out_dir)

    if not epa_lmdb_path.exists():
        raise FileNotFoundError(f'EPA LMDB not found: {epa_lmdb_path}')

    # Read all EPA vectors
    surfaces: list[str] = []
    vectors:  list[list[float]] = []

    env = lmdb.open(str(epa_lmdb_path), max_dbs=5, readonly=True)
    with env.begin() as txn:
        db = env.open_db(b'epa', txn=txn)
        cur = txn.cursor(db=db)
        for k, v in cur.iternext():
            try:
                raw = k.decode('utf-8')
                surface = raw[3:] if raw.startswith('en|') else raw
                e, p, a = _EPA_STRUCT.unpack(v)
                surfaces.append(surface)
                vectors.append([e, p, a])
            except Exception:
                continue
    env.close()

    n = len(surfaces)
    fp = _fingerprint(epa_lmdb_path)
    print(f'  EPA entries: {n:,}  fingerprint: {fp}')

    if dry_run:
        return {'total': n, 'fingerprint': fp, 'dry_run': True}

    # Build FAISS flat L2 index (exact search, small enough for flat)
    t0 = time.perf_counter()
    mat = np.array(vectors, dtype=np.float32)
    index = faiss.IndexFlatL2(DIM)
    index.add(mat)
    elapsed = round(time.perf_counter() - t0, 3)

    print(f'  Index built: {index.ntotal:,} vectors, dim={DIM}, elapsed={elapsed}s')

    # Write index
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / INDEX_NAME
    faiss.write_index(index, str(index_path))
    print(f'  Index saved: {index_path}')

    # Write surface list (parallel to FAISS internal IDs)
    surfaces_path = out_dir / 'dictionary.faiss.surfaces.json'
    with open(surfaces_path, 'w', encoding='utf-8') as f:
        json.dump(surfaces, f, ensure_ascii=False)

    # Write metadata
    meta = {
        'epa_lmdb_path':   str(epa_lmdb_path),
        'epa_fingerprint': fp,
        'entry_count':     n,
        'dim':             DIM,
        'index_type':      'FlatL2',
        'index_path':      str(index_path),
        'surfaces_path':   str(surfaces_path),
        'build_elapsed_s': elapsed,
        'built_at':        time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    meta_path = out_dir / META_NAME
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)
    print(f'  Meta saved:  {meta_path}')

    return meta


def load_index(out_dir: Path = DEFAULT_OUT) -> tuple:
    """
    Load the FAISS index + surface list for query use.
    Returns (index, surfaces, meta).
    """
    out_dir = Path(out_dir)
    meta_path = out_dir / META_NAME
    with open(meta_path) as f:
        meta = json.load(f)
    index    = faiss.read_index(str(out_dir / INDEX_NAME))
    surfaces_path = out_dir / 'dictionary.faiss.surfaces.json'
    with open(surfaces_path, encoding='utf-8') as f:
        surfaces = json.load(f)
    return index, surfaces, meta


def query(index, surfaces: list[str], epa_vec: tuple[float, float, float], k: int = 30):
    """
    Nearest-neighbor query. Returns list of (surface, l2_distance).
    """
    q = np.array([[epa_vec[0], epa_vec[1], epa_vec[2]]], dtype=np.float32)
    D, I = index.search(q, k)
    return [(surfaces[i], float(d)) for d, i in zip(D[0], I[0]) if i >= 0]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--epa-db',  default=str(DEFAULT_EPA_DB))
    ap.add_argument('--out-dir', default=str(DEFAULT_OUT))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--query',   nargs=3, type=float, metavar=('E', 'P', 'A'),
                    help='After building, run a test query (E P A)')
    args = ap.parse_args()

    print(f'[faiss_builder] epa-db={args.epa_db}')
    meta = build_faiss_index(Path(args.epa_db), Path(args.out_dir), args.dry_run)

    if args.query and not args.dry_run:
        print(f'\n  Test query E={args.query[0]} P={args.query[1]} A={args.query[2]}:')
        index, surfaces, _ = load_index(Path(args.out_dir))
        results = query(index, surfaces, tuple(args.query), k=10)
        for surface, dist in results:
            print(f'    {surface:<30} dist={dist:.4f}')


if __name__ == '__main__':
    main()
