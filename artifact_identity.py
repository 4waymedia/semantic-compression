"""
artifact_identity.py -- ONE identity convention for every build artifact.

Every artifact a dictionary build produces (dictionary, facets, epa, templates,
meta) carries the SAME identity shape, and the package manifest.json references
them all in an `artifacts` registry. This makes the whole stack reproducible and
pairable by fingerprint -- the same rule that already pins dictionary<->model.

Identity record:
    kind        dictionary | facets | epa | templates | meta
    present     True = built; False = reserved (not yet produced)
    version     schema/format version of THIS artifact
    status      staged | frozen | locked
    fingerprint deterministic content hash (None if absent)
    key_scheme  base64_id | surface | lang_surface | none
    bound_refs  what it is pinned to: {dictionary, model, global_epa, corpus}
    stamped_utc when this identity was written
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

STATUSES    = ("staged", "frozen", "locked")
KINDS       = ("dictionary", "facets", "epa", "templates", "meta")
KEY_SCHEMES = ("base64_id", "surface", "lang_surface", "none")


def fingerprint_pairs(pairs) -> str:
    """Deterministic sha256 over an iterable of (key, value) -- sorted, tab-joined."""
    h = hashlib.sha256()
    for k, v in sorted(pairs):
        h.update(f"{k}\t{v}\n".encode("utf-8"))
    return h.hexdigest()


def make_identity(kind: str, *, version, fingerprint=None, status="staged",
                  key_scheme="base64_id", bound_refs=None, present=True,
                  notes=None) -> dict:
    assert kind in KINDS, f"bad kind {kind!r}"
    assert status in STATUSES, f"bad status {status!r}"
    assert key_scheme in KEY_SCHEMES, f"bad key_scheme {key_scheme!r}"
    return {
        "kind": kind,
        "present": present,
        "version": version,
        "status": status,
        "fingerprint": fingerprint,
        "key_scheme": key_scheme,
        "bound_refs": {k: v for k, v in (bound_refs or {}).items() if v is not None},
        "stamped_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "notes": notes,
    }


def validate_registry(reg: dict) -> list[str]:
    """Return a list of problems ([] == valid)."""
    problems = []
    dfp = (reg.get("dictionary") or {}).get("fingerprint")
    for kind in ("facets", "epa", "templates", "meta"):
        a = reg.get(kind)
        if a and a.get("present"):
            bound = a.get("bound_refs", {}).get("dictionary")
            if bound and dfp and bound != dfp:
                problems.append(f"{kind} bound to dictionary {bound[:12]}… != {dfp[:12]}…")
            if not bound and dfp:
                problems.append(f"{kind} present but not bound to the dictionary fingerprint")
    for kind, a in reg.items():
        if a.get("status") == "locked" and not a.get("bound_refs", {}).get("model"):
            problems.append(f"{kind} is locked but has no bound model")
    return problems


def _dictionary_fingerprint(lmdb_path: Path) -> str | None:
    """True dictionary fp = hash over (surface, id) from the forward DB."""
    try:
        import lmdb
    except Exception:
        return None
    if not Path(lmdb_path).exists():
        return None
    env = lmdb.open(str(lmdb_path), readonly=True, max_dbs=4, lock=False)
    try:
        fwd = env.open_db(b"forward", create=False)
        with env.begin() as txn:
            pairs = ((k.decode("utf-8", "replace"), v.decode("utf-8", "replace"))
                     for k, v in txn.cursor(db=fwd))
            return fingerprint_pairs(pairs)
    finally:
        env.close()


def _has_subdb(lmdb_path: Path, name: bytes) -> bool:
    try:
        import lmdb
    except Exception:
        return False
    if not Path(lmdb_path).exists():
        return False
    env = lmdb.open(str(lmdb_path), readonly=True, max_dbs=6, lock=False)
    try:
        env.open_db(name, create=False)
        return True
    except lmdb.Error:
        return False
    finally:
        env.close()


def _epa_fingerprint(lmdb_path: Path) -> str | None:
    """epa fp = hash over sorted (id, E, P, A) of the matched b'epa' table."""
    try:
        import lmdb, struct
    except Exception:
        return None
    if not Path(lmdb_path).exists():
        return None
    env = lmdb.open(str(lmdb_path), readonly=True, max_dbs=8, lock=False)
    try:
        epa = env.open_db(b"epa", create=False)
    except Exception:
        env.close(); return None
    try:
        with env.begin() as txn:
            pairs = ((k.decode("utf-8", "replace"),
                      ",".join(f"{x:.4f}" for x in struct.unpack("<fff", v)))
                     for k, v in txn.cursor(db=epa))
            return fingerprint_pairs(pairs)
    finally:
        env.close()


def registry_from_package(pkg_dir: str | Path) -> dict:
    """Compose the artifacts registry from a build package's on-disk state."""
    pkg = Path(pkg_dir)
    man = json.loads((pkg / "manifest.json").read_text()) if (pkg / "manifest.json").exists() else {}
    lmdb_path = pkg / "dictionary.lmdb"
    dfp = _dictionary_fingerprint(lmdb_path)
    status = man.get("dictionary_status", "staged")
    model = man.get("bound_model")
    corpus_fp = man.get("corpus_fingerprint")
    version = (man.get("build_params") or {}).get("format_version") or 3

    reg = {}
    reg["dictionary"] = make_identity(
        "dictionary", version=version, fingerprint=dfp, status=status,
        key_scheme="base64_id", bound_refs={"corpus": corpus_fp, "model": model})

    fs = pkg / "facets_stats.json"
    if fs.exists():
        f = json.loads(fs.read_text())
        reg["facets"] = make_identity(
            "facets", version=f.get("facets_format_version", 1),
            fingerprint=f.get("dictionary_fingerprint"), status=status,
            key_scheme="base64_id", bound_refs={"dictionary": dfp})
    else:
        reg["facets"] = make_identity("facets", version=1, present=False,
                                      status=status, notes="not built")

    # epa: present iff the package LMDB carries a matched b'epa' sub-db
    epa_present = _has_subdb(lmdb_path, b"epa")
    epa_stats = pkg / "epa_stats.json"
    global_epa = (json.loads(epa_stats.read_text()).get("global_epa_version")
                  if epa_stats.exists() else None)
    reg["epa"] = make_identity(
        "epa", version=1, present=epa_present, status=status,
        fingerprint=_epa_fingerprint(lmdb_path) if epa_present else None,
        key_scheme="base64_id" if epa_present else "none",
        bound_refs={"dictionary": dfp, "global_epa": global_epa} if epa_present else None,
        notes=None if epa_present else "EPA match step not yet run (see EPA.md)")

    meta_present = (pkg / "meta.db").exists()
    meta_stats = pkg / "meta_stats.json"
    meta_fp = (json.loads(meta_stats.read_text()).get("meta_fingerprint")
               if meta_stats.exists() else None)
    reg["meta"] = make_identity(
        "meta", version=1, present=meta_present, status=status,
        fingerprint=meta_fp, key_scheme="surface", bound_refs={"dictionary": dfp},
        notes=None if meta_present else "meta DB not built (spec-meta-db.md)")

    reg["templates"] = make_identity("templates", version=1, present=False,
                                     status=status, notes="System 2 (mneme), not matched")
    return reg


def write_registry(pkg_dir: str | Path) -> dict:
    """Compose + merge the artifacts registry into the package manifest.json."""
    pkg = Path(pkg_dir)
    reg = registry_from_package(pkg)
    man_path = pkg / "manifest.json"
    man = json.loads(man_path.read_text()) if man_path.exists() else {}
    man["artifacts"] = reg
    man["artifacts_problems"] = validate_registry(reg)
    man_path.write_text(json.dumps(man, indent=2), encoding="utf-8")
    return reg


if __name__ == "__main__":
    import sys
    reg = write_registry(sys.argv[1])
    for kind, a in reg.items():
        fp = (a["fingerprint"] or "")[:16]
        print(f"  {kind:<11} present={str(a['present']):<5} v{a['version']} "
              f"status={a['status']:<7} key={a['key_scheme']:<11} fp={fp:<16} "
              f"bound={list(a['bound_refs'])}")
    print("  problems:", validate_registry(reg) or "none")
