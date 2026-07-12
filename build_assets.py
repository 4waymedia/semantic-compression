"""
build_assets.py -- rebuild ALL derived assets for a dictionary build, in order.

Every dictionary (re)build invalidates its derived assets. This driver re-derives
them in dependency order, rebuilding only what is stale (fingerprint-guarded), with
the atomic-write + reproducibility discipline in
docs/compression/spec-asset-pipeline.md. It ORCHESTRATES the per-asset scripts; each
script owns its own logic and atomic write. The driver never writes an asset itself.

    python build_assets.py db/builds/<name>              # rebuild stale only
    python build_assets.py db/builds/<name> --dry-run    # plan; run nothing
    python build_assets.py db/builds/<name> --force      # rebuild everything
    python build_assets.py db/builds/<name> --only 5,8   # just these stages
    python build_assets.py db/builds/<name> --from 3     # stage 3 onward
    python build_assets.py db/builds/<name> --device cuda # for the embed stage

Staleness (per stage): rebuild iff --force, OR the dictionary signature changed
since the recorded run, OR the stage's script is newer than the recorded run, OR a
declared output sentinel is missing. Else SKIP. Ledger: <pkg>/assets_pipeline.json.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_suite

SC = Path(__file__).resolve().parent                 # semantic_compression/
ROOT = SC.parent                                     # R-D-concepts/
BROWSER_TOOLS = ROOT / "ELO-Browser" / "tools"
# O3: browser assets land in a PER-DICTIONARY subdir so shipped products never
# collide. The runtime selects a product dir; the default is keyed by build name.
BROWSER_DICT_ROOT = ROOT / "ELO-Browser" / "elo-browser" / "src-tauri" / "dictionary"
PY = sys.executable

# Which suite asset (build_suite.ORDER) gates each stage. None = always run
# (core/lifecycle: registry, stamp, verify). Stages whose asset is not in the
# package's declared suite are reported "off (not in suite)" and skipped.
# Stage 8 (neighbours.bin) rides on the 768-d index, so it gates on `vectors`, not
# `browser` — a browser build without vectors ships epa.bin+facets.bin but no
# neighbours (correctly "off"), instead of failing for a missing index.
STAGE_ASSET = {1: "facets", 2: "meta", 3: "epa", 4: "meta_layer2", 5: "vectors",
               6: "browser", 7: "browser", 8: "vectors",
               9: None, 10: None, 11: None}


def _sig(path: Path) -> str:
    """Cheap change-signature for a file or lmdb dir (size+mtime of data.mdb)."""
    p = path / "data.mdb" if path.is_dir() and (path / "data.mdb").exists() else path
    if not p.exists():
        return "absent"
    st = p.stat()
    return hashlib.sha256(f"{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:16]


def _have(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


# ---------------------------------------------------------------------------
# The cascade. Each stage: number, name, the script (None=not written yet),
# argv(pkg), cwd, output sentinels(pkg), and any heavy dep it needs.
# ---------------------------------------------------------------------------

def _stages(pkg: Path, device: str | None, browser_out: Path,
            stamp_release: str, stamp_status: str):
    lmdb = pkg / "dictionary.lmdb"
    vocab = pkg / f"{pkg.name}.browser.json"
    dev = ["--device", device] if device else []
    # Embed scope = the browser vocab cut (profile-cuts.json 'full' content_size),
    # so the 768-d index covers exactly the vocab n-range. profile-cuts.json is
    # emitted by the CORE build (before stage 5), so no ordering inversion.
    limit = []
    pc = pkg / "profile-cuts.json"
    if pc.exists():
        try:
            cs = json.loads(pc.read_text(encoding="utf-8"))["full"]["content_size"]
            limit = ["--limit", str(int(cs))]
        except Exception:
            limit = []
    return [
        dict(n=1, name="facets", script=SC / "facet_builder.py", cwd=SC, dep=None,
             argv=["--db", str(lmdb), "--overrides", "data/facet_overrides.tsv"],
             out=[pkg / "facets_stats.json"]),
        dict(n=2, name="meta-L1", script=SC / "meta_builder.py", cwd=SC, dep=None,
             argv=[str(pkg)], out=[pkg / "meta.db", pkg / "meta_stats.json"]),
        dict(n=3, name="epa", script=SC / "epa_match.py", cwd=SC, dep="lmdb",
             argv=[str(pkg)], out=[pkg / "epa_stats.json"]),
        dict(n=4, name="meta-L2", script=SC / "meta_layer2.py", cwd=SC, dep="lmdb",
             argv=[str(pkg)], out=[pkg / "meta_layer2_stats.json"]),
        dict(n=5, name="denotative", script=SC / "denotative_index.py", cwd=SC,
             dep="sentence_transformers", multi=[
                 ["embed", "--meta", str(pkg / "meta.db"), "--out", str(pkg)] + dev + limit,
                 ["finalize", "--meta", str(pkg / "meta.db"), "--out", str(pkg)]],
             out=[pkg / "dictionary.denotative.json"]),
        dict(n=6, name="browser-vocab", script=SC / "export_browser_vocab.py", cwd=SC,
             dep=None, argv=["--build", str(pkg), "--cut", "full"], out=[vocab]),
        dict(n=7, name="browser-epa+facets", script=BROWSER_TOOLS / "export_browser_assets.py",
             cwd=ROOT, dep="lmdb", argv=["--build", str(pkg), "--out", str(browser_out)],
             out=[browser_out / "epa.bin", browser_out / "facets.bin",
                  browser_out / "assets.meta.json"]),
        dict(n=8, name="browser-neighbours", script=BROWSER_TOOLS / "export_neighbours.py",
             cwd=ROOT, dep="faiss", argv=["--build", str(pkg), "--index", str(pkg),
             "--out", str(browser_out)], out=[browser_out / "neighbours.bin"]),
        dict(n=9, name="registry", script=None, cwd=SC, dep=None, argv=[],
             out=[pkg / "manifest.json"],
             note="artifact_identity.write_registry (run by build_from_spec)"),
        dict(n=10, name="stamp", script=SC / "stamp_meta.py", cwd=SC, dep=None,
             argv=["--db", str(lmdb), "--release", stamp_release,
                   "--status", stamp_status], out=[]),
        dict(n=11, name="verify", script=SC / "verify_facets.py", cwd=SC, dep=None,
             argv=[], out=[], gate=True),
    ]


def _stale(stage, pkg, ledger, dict_sig) -> tuple[bool, str]:
    rec = ledger.get(str(stage["n"]))
    for o in stage["out"]:
        if not Path(o).exists():
            return True, f"output missing: {Path(o).name}"
    if rec is None:
        return True, "never run"
    if rec.get("dict_sig") != dict_sig:
        return True, "dictionary changed"
    scr = stage["script"]
    if scr and Path(scr).exists() and Path(scr).stat().st_mtime > rec.get("script_mtime", 0):
        return True, "script updated"
    return False, "up to date"


def _run(stage, ledger, dict_sig, dry) -> str:
    scr = stage["script"]
    if scr is None:
        return "PENDING (no script; " + stage.get("note", "external") + ")"
    if not Path(scr).exists():
        return f"PENDING (missing {Path(scr).name})"
    if stage["dep"] and stage["dep"] != "lmdb" and not _have(stage["dep"]):
        return f"SKIPPED (missing dep: {stage['dep']})"
    cmds = stage.get("multi") or [stage["argv"]]
    if dry:
        for c in cmds:
            print(f"      would run: {Path(scr).name} {' '.join(c)}  (cwd={stage['cwd'].name})")
        return "DRY"
    for c in cmds:
        r = subprocess.run([PY, str(scr), *c], cwd=str(stage["cwd"]))
        if r.returncode != 0:
            return f"FAILED (exit {r.returncode})"
    ledger[str(stage["n"])] = {"name": stage["name"], "dict_sig": dict_sig,
                               "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               "script_mtime": Path(scr).stat().st_mtime,
                               "outputs": [str(o) for o in stage["out"]]}
    return "OK"


def _write_ledger(pkg, ledger):
    tmp = pkg / "assets_pipeline.json.tmp"
    tmp.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    os.replace(tmp, pkg / "assets_pipeline.json")   # atomic


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("pkg", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default=None, help="comma stage numbers")
    ap.add_argument("--from", dest="frm", type=int, default=None)
    ap.add_argument("--device", default=None)
    ap.add_argument("--browser-out", default=None,
                    help="O3: browser asset dir (default: <root>/dictionary/<name>/)")
    ap.add_argument("--release", default=None, help="O4: stamp release (default: from spec_meta)")
    ap.add_argument("--status", default=None, help="O4: stamp status (default: from spec_meta)")
    a = ap.parse_args()

    pkg = a.pkg.resolve()
    if not (pkg / "dictionary.lmdb").exists():
        sys.exit(f"no dictionary.lmdb in {pkg}")

    # Read the ONE declaration from the package manifest: suite + spec_meta.
    man = {}
    mpath = pkg / "manifest.json"
    if mpath.exists():
        man = json.loads(mpath.read_text(encoding="utf-8"))
    rec_suite = man.get("suite") or {}
    if rec_suite.get("enabled"):
        enabled = set(rec_suite["enabled"])                 # authoritative (recorded)
    else:
        enabled = build_suite.declared_set(man.get("build_params", {}))  # recompute
    smeta = man.get("spec_meta", {})

    # O3: per-dictionary browser output.
    browser_out = Path(a.browser_out).resolve() if a.browser_out else BROWSER_DICT_ROOT / pkg.name
    # O4: stamp release/status from spec_meta unless overridden.
    stamp_release = a.release or smeta.get("release") or (
        f"v{smeta['version']}" if smeta.get("version") else "v0")
    stamp_status = a.status or smeta.get("status") or "staged"

    lpath = pkg / "assets_pipeline.json"
    ledger = json.loads(lpath.read_text()) if lpath.exists() else {}
    dict_sig = _sig(pkg / "dictionary.lmdb")
    only = {int(x) for x in a.only.split(",")} if a.only else None

    print(f"assets for {pkg.name}   dict_sig={dict_sig}   suite={sorted(enabled)}"
          + ("   [DRY RUN]" if a.dry_run else ""))
    print(f"  browser_out={browser_out}   stamp={stamp_release}/{stamp_status}")
    print(f"{'#':>2}  {'stage':<20}{'state':<26}action")
    print("-" * 70)
    for st in _stages(pkg, a.device, browser_out, stamp_release, stamp_status):
        if only is not None and st["n"] not in only:
            continue
        if a.frm is not None and st["n"] < a.frm:
            continue
        asset = STAGE_ASSET.get(st["n"])
        if asset is not None and asset not in enabled:
            print(f"{st['n']:>2}  {st['name']:<20}{('off (not in suite)'):<26}skip")
            continue
        stale, why = (True, "forced") if a.force else _stale(st, pkg, ledger, dict_sig)
        if not stale:
            print(f"{st['n']:>2}  {st['name']:<20}{'up to date':<26}skip")
            continue
        # Rebuild hygiene: when the DICTIONARY changed (or forced/first run), the
        # denotative embed must start clean — old vectors are for a dead vocab. On
        # "output missing" we leave it to RESUME an interrupted embed.
        if st["n"] == 5 and st.get("multi") and (
                why in ("forced", "dictionary changed", "never run")):
            if "--fresh" not in st["multi"][0]:
                st["multi"][0] = st["multi"][0] + ["--fresh"]
        print(f"{st['n']:>2}  {st['name']:<20}{('stale: '+why):<26}", end="")
        res = _run(st, ledger, dict_sig, a.dry_run)
        print(res)
    if not a.dry_run:
        _write_ledger(pkg, ledger)
        print(f"\nledger -> {lpath.name}")


if __name__ == "__main__":
    main()
