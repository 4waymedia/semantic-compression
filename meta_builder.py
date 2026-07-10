"""
meta_builder.py -- derive the per-surface meta DB for a dictionary build.

Reads the package's forward sub-DB (surface -> id) and writes meta.db (SQLite),
keyed by SURFACE (stable; IDs are provisional), using the System-1 deterministic
derivation in meta_fields.derive_meta. System-2 columns (epa/polarity/agency/...)
are reserved (NULL) until the EPA layer fills them. Re-derived per build; emits a
meta_fingerprint over the deterministic subset. Spec: docs/compression/spec-meta-db.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
import lmdb
from meta_fields import derive_meta, S2_RESERVED
try:
    from facets import load_overrides
except Exception:
    load_overrides = None

DET_COLS = ("bucket", "utility", "logic_cues", "flags", "abstraction",
            "causality", "temporality", "scope", "domain", "register",
            "complement")
COLS = ("surface", "id", "tier", "kind") + DET_COLS + S2_RESERVED + ("method", "meta_layer")


def _j(v):
    return json.dumps(v) if isinstance(v, (list, dict)) else v


_HERE = Path(__file__).resolve().parent


def _resolve_overrides(p):
    """Find the overrides file relative to CWD, then to this module. The
    meta_fingerprint depends on it, so resolution must not depend on cwd."""
    q = Path(p)
    if q.exists():
        return q
    q2 = _HERE / p
    return q2 if q2.exists() else None


def build_meta(lmdb_path, out_db, overrides_path="data/facet_overrides.tsv",
               *, require_overrides=True) -> dict:
    # The facet overrides feed assign_facet -> bucket/flags -> the DETERMINISTIC
    # fingerprint. Silently skipping them (the old `except: ov = None`) produced a
    # different meta_fingerprint depending on the cwd. Resolve module-relative and
    # fail loudly instead.
    ov = None
    ov_file = _resolve_overrides(overrides_path)
    if ov_file is None:
        if require_overrides:
            raise FileNotFoundError(
                f"facet overrides not found: {overrides_path!r} (cwd={Path.cwd()}). "
                "meta_fingerprint depends on it; refusing to build a silently-different "
                "meta.db. Pass require_overrides=False to build without them.")
    elif load_overrides:
        ov = load_overrides(str(ov_file))
    ov_sha = (hashlib.sha256(ov_file.read_bytes()).hexdigest()[:12]
              if ov_file is not None else None)

    env = lmdb.open(str(lmdb_path), readonly=True, max_dbs=8, lock=False)
    fwd = env.open_db(b"forward", create=False)
    rows = []
    fp = hashlib.sha256()
    with env.begin() as txn:
        for k, v in txn.cursor(db=fwd):
            surface = k.decode("utf-8", "replace")
            sid = v.decode("utf-8", "replace")
            m = derive_meta(surface, ov)
            m["id"] = sid
            m["tier"] = max(0, len(sid) - 1)
            rows.append(tuple(_j(m.get(c)) for c in COLS))
            # deterministic-subset fingerprint
            det = "|".join(str(m.get(c)) for c in DET_COLS)
            fp.update(f"{surface}\t{det}\n".encode())
    env.close()
    fingerprint = fp.hexdigest()

    out_db = Path(out_db)
    # SQLite can't journal on the mounted FS -> build in local /tmp, then copy.
    tmp_db = Path(tempfile.gettempdir()) / f"_meta_{os.getpid()}.db"
    if tmp_db.exists(): tmp_db.unlink()
    con = sqlite3.connect(tmp_db)
    con.execute(f"CREATE TABLE meta ({', '.join(c + (' TEXT' if c not in ('tier','meta_layer','epa_e','epa_p','epa_a') else (' REAL' if c.startswith('epa_') else ' INTEGER')) for c in COLS)}, PRIMARY KEY(surface))")
    con.executemany(f"INSERT OR REPLACE INTO meta ({','.join(COLS)}) VALUES ({','.join('?'*len(COLS))})", rows)
    con.execute("CREATE TABLE meta_info (key TEXT PRIMARY KEY, value TEXT)")
    info = {"meta_format_version": "1", "meta_layer": "1",
            "meta_fingerprint": fingerprint, "rows": str(len(rows)),
            # provenance of the fingerprint's inputs (not itself fingerprinted)
            "overrides_applied": "1" if ov else "0",
            "overrides_sha": ov_sha or ""}
    con.executemany("INSERT INTO meta_info VALUES (?,?)", list(info.items()))
    con.execute("CREATE INDEX idx_bucket ON meta(bucket)")
    con.execute("CREATE INDEX idx_domain ON meta(domain)")
    con.commit()
    # quick distribution for the report (read before copy)
    dist = {b: c for b, c in con.execute("SELECT bucket, COUNT(*) FROM meta GROUP BY bucket")}
    abst = con.execute("SELECT COUNT(*) FROM meta WHERE abstraction='abstract'").fetchone()[0]
    causal = con.execute("SELECT COUNT(*) FROM meta WHERE causality IS NOT NULL AND causality!='null'").fetchone()[0]
    con.close()
    # N2: atomic replace -- meta.db can never be observed 0-byte / partial.
    # Stage next to the target, fsync, VERIFY, then rename over. There is NO
    # overwrite-copy fallback: copying onto a live SQLite file is not atomic and a
    # partial write leaves a malformed image (observed: elo-browser-v01/meta.db,
    # 2026-07-08). Failing loudly with a valid staged DB beats silent corruption.
    staged = out_db.with_suffix(".db.tmp")
    shutil.copyfile(tmp_db, staged)
    with open(staged, "rb") as fh:
        os.fsync(fh.fileno())
    _v = sqlite3.connect(staged)
    _ok = _v.execute("PRAGMA integrity_check").fetchone()[0]
    _v.close()
    if _ok != "ok":
        raise RuntimeError(f"staged meta.db failed integrity_check: {_ok!r}")
    try:
        os.replace(staged, out_db)
    except OSError as e:
        raise RuntimeError(
            f"could not atomically replace {out_db} ({e}). A VALID DB is staged at "
            f"{staged} -- move it into place manually. Refusing to overwrite in "
            "place: a non-atomic copy corrupts a live SQLite file.") from e
    tmp_db.unlink()
    # verify: written DB opens and row count matches what we built
    vcon = sqlite3.connect(out_db)
    vrows = vcon.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
    vcon.close()
    if vrows != len(rows):
        raise RuntimeError(f"meta.db verify failed: {vrows} rows on disk != {len(rows)} built")

    stats = {"rows": len(rows), "meta_fingerprint": fingerprint, "out": str(out_db),
             "bucket_dist": dist, "abstract_marked": abst, "causality_marked": causal,
             "meta_layer": 1}
    (out_db.parent / "meta_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats


if __name__ == "__main__":
    pkg = Path(sys.argv[1])
    s = build_meta(pkg / "dictionary.lmdb", pkg / "meta.db")
    print(json.dumps(s, indent=2))
