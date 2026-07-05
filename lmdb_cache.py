"""
lmdb_cache.py
Process-wide cache of read-only LMDB environments, keyed by real path.

py-lmdb refuses to open the same environment twice in one process (it raises
"The environment '...' is already open in this process."). Several components
read the same dictionary -- the verbalizer substrate, the seed-surface
segmenter, the inverted-index fingerprint reader -- so opening independently
collides. Routing every read-only open through this cache means there is ever
only ONE environment per path, shared safely.

Cached environments are opened read-only with lock=False and live for the
process lifetime (the OS reclaims them at exit). Do NOT close a cached env from
a component's own `close()`; call `close_all()` only at a true shutdown.
"""
from __future__ import annotations

import os
import sys
import lmdb

# Registry lives on `sys` so it is a true process singleton even if this module
# is imported under two names (`lmdb_cache` and `semantic_compression.lmdb_cache`
# resolve to different module objects, but share this one dict).
_ENVS: dict = sys.__dict__.setdefault('_eloai_lmdb_envs', {})
_MAX_DBS = 16   # generous: covers forward/reverse/vfacets/meta and EPA sub-dbs


def get_env(path, max_dbs: int = _MAX_DBS) -> "lmdb.Environment":
    """Return the cached read-only Environment for `path`, opening it once."""
    key = os.path.realpath(str(path))
    env = _ENVS.get(key)
    if env is None:
        env = lmdb.open(str(path), max_dbs=max(_MAX_DBS, max_dbs),
                        readonly=True, lock=False)
        _ENVS[key] = env
    return env


def is_cached(path) -> bool:
    return os.path.realpath(str(path)) in _ENVS


def close_all() -> None:
    """Close every cached env. Only at process shutdown -- invalidates all handles."""
    for env in _ENVS.values():
        try:
            env.close()
        except Exception:
            pass
    _ENVS.clear()
