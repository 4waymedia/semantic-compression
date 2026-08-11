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
               9: None, 10: None, 11: None, 12: None,
               # 13 = vfacets, now a FIRST-CLASS suite asset (2026-08-10 review):
               # gating on "epa" made every epa-declaring build inherit it silently
               # with no way to decline. It gates on its own declaration; the
               # epa REQUIREMENT lives in build_suite.DEPS["vfacets"] = ["epa"],
               # which fails fast at resolve time instead of writing 437,995 rows
               # of NEUTRAL/UNKNOWN that look like data.
               13: "vfacets"}


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
        # VFACETS -- the verbalizer's facet channel (b'vfacets' sub-db).
        #
        # WHY IT IS HERE NOW: it was step 7 of PROCESS.md and never a stage, so
        # no build produced it. Measured 2026-08-10: db/dictionary.lmdb carried
        # 437,995 vfacet records and db/builds/elo-browser-v01c/ carried none --
        # from the SAME build (identical fingerprint 9a77e623…, identical ids on
        # 20,000/20,000 probed surfaces). Someone ran the pass by hand against
        # one path. The verbalizer's lookup_vfacet worked only because paths.py
        # still defaults to that legacy path, and 06's facet_recall ranks on it.
        #
        # THIS STAGE IS THE DETERMINISTIC HALF ONLY -- polarity (EPA.E
        # threshold), temporality (suffix heuristics), domain (word lists). No
        # model, no network, seconds to run. `agency` and `direction` stay
        # UNKNOWN and are patched by vfacet_llm.py as a separate ENRICHMENT,
        # deliberately not in the cascade: a build stage that needs an LLM is a
        # build that cannot be reproduced offline. 3 of 5 fields for free beats
        # 0 of 5 waiting on the other 2.
        #
        # NUMBERING: n=13 is an append-only id, NOT its position -- execution
        # follows LIST order (the loop iterates _stages()), and this must run
        # after epa/meta-L2. Numbered high so every existing `--only`/`--from`
        # invocation and the runbook's stage table keep their meaning.
        # Renumbering to 5 and shifting 5-12 up is the tidy follow-up; it is a
        # separate change because it rewrites a documented CLI contract.
        dict(n=13, name="vfacets", script=SC / "vfacet_builder.py", cwd=SC,
             dep="lmdb",
             argv=["--db", str(lmdb),
                   "--epa-db", str(ROOT / "Memory" / "data" / "epa_substrate.lmdb")],
             out=[pkg / "vfacets_stats.json"]),
        dict(n=5, name="denotative", script=SC / "denotative_index.py", cwd=SC,
             dep="sentence_transformers", multi=[
                 ["embed", "--meta", str(pkg / "meta.db"), "--out", str(pkg)] + dev + limit,
                 ["finalize", "--meta", str(pkg / "meta.db"), "--out", str(pkg)]],
             out=[pkg / "dictionary.denotative.json"]),
        dict(n=6, name="browser-vocab", script=SC / "export_browser_vocab.py", cwd=SC,
             dep=None, argv=["--build", str(pkg), "--cut", "full"], out=[vocab]),
        # --out-root, not --out: the exporter appends the build name itself, so the
        # versioned folder is guaranteed rather than assembled by each caller.
        dict(n=7, name="browser-epa+facets", script=BROWSER_TOOLS / "export_browser_assets.py",
             cwd=ROOT, dep="lmdb",
             argv=["--build", str(pkg), "--out-root", str(browser_out.parent)],
             out=[browser_out / "epa.bin", browser_out / "facets.bin",
                  browser_out / "assets.meta.json",
                  browser_out / f"{pkg.name}.browser.json"]),
        dict(n=8, name="browser-neighbours", script=BROWSER_TOOLS / "export_neighbours.py",
             cwd=ROOT, dep="faiss", argv=["--build", str(pkg), "--index", str(pkg),
             "--out", str(browser_out)], out=[browser_out / "neighbours.bin"]),
        # RE-DERIVE THE REGISTRY *AFTER* THE ASSETS EXIST. Previously script=None: the
        # manifest was written once by build_from_spec at CORE-build time, before epa
        # (stage 3) / meta_layer2 / vectors ran, so it froze a pre-epa view --
        # artifacts.epa.present=false while epa.bin shipped (spec-publish-dictionary
        # sec 1.2). Running write_registry here, and always (gate), makes the manifest
        # reflect the finished package and tags deliverables build-vs-bundle (sec 1.3).
        dict(n=9, name="registry", script=SC / "artifact_identity.py", cwd=SC, dep=None,
             argv=[str(pkg)], out=[pkg / "manifest.json"], gate=True),
        dict(n=10, name="stamp", script=SC / "stamp_meta.py", cwd=SC, dep=None,
             argv=["--db", str(lmdb), "--release", stamp_release,
                   "--status", stamp_status], out=[]),
        # STAGE 11 RUNS BOTH GATES, EACH POINTED AT THIS PACKAGE.
        #
        # DICTIONARY-BUILD-RUNBOOK.md and spec-asset-pipeline.md have always listed
        # verify_lossless + verify_facets here, but the code ran only verify_facets —
        # and with argv=[], so it fell back to the legacy 'db/dictionary.lmdb' default
        # and gated every build against a database that was not the one being built.
        # verify_lossless, the byte-exact round-trip check, ran against no build at all.
        #
        # bench_dict_efficiency is deliberately NOT here: it needs the eval corpora and
        # takes minutes. It is a measurement, not a pass/fail gate — see the runbook.
        dict(n=11, name="verify", script=SC / "verify_facets.py", cwd=SC, dep=None,
             multi=[["--db", str(lmdb)]], out=[], gate=True),
        dict(n=12, name="verify-lossless", script=SC / "verify_lossless.py", cwd=ROOT,
             dep=None, argv=["--db", str(lmdb)], out=[], gate=True),
    ]


def _preflight_device(device: str, allow_cpu: bool) -> None:
    """CUDA IS THE DEFAULT AND ITS ABSENCE IS A HARD STOP.

    Stage 5 embeds the whole vocab cut (~262k surfaces) with all-mpnet-base-v2. On the
    5090 that is minutes; on CPU it is hours — slow enough that people cancel the run,
    which is how a package ends up with no neighbours.bin.

    We check BEFORE any stage runs, because the failure used to surface 4 stages deep:
    stage 5 died with "Torch not compiled with CUDA enabled", stage 8 then failed for a
    missing index, and the pipeline printed both and carried on to a clean-looking
    finish. Ten minutes of work to learn something knowable in 50ms.

    On the diagnosis: torch's CPU and CUDA builds are the same package name, so only one
    can be installed. PyPI ships CPU-only wheels on Windows and the CUDA builds live at
    download.pytorch.org — so `pip install` of anything depending on torch (a
    sentence-transformers upgrade, faiss, an unpinned resolve) silently replaces a
    working cu128 install. Hence printing the interpreter path AND the torch build: the
    two real causes are "wrong venv" and "something overwrote it", and they look
    identical from the error message alone.
    """
    if device != "cuda":
        return
    try:
        import torch                                   # noqa: PLC0415
    except Exception as e:
        sys.exit(f"[preflight] --device cuda but torch will not import: {e}\n"
                 f"            interpreter: {PY}")
    if torch.cuda.is_available():
        n = torch.cuda.get_device_name(0)
        print(f"  device: cuda -> {n}  (torch {torch.__version__}, cuda {torch.version.cuda})")
        return
    if allow_cpu:
        print(f"  device: CPU FALLBACK (--allow-cpu). torch {torch.__version__} "
              f"has no CUDA; the embed stage will take hours.")
        return
    sys.exit(
        "\n[preflight] --device cuda, but this torch cannot use the GPU.\n"
        f"    interpreter : {PY}\n"
        f"    torch       : {torch.__version__}\n"
        f"    torch.version.cuda : {torch.version.cuda}   <- None means a CPU-only build\n"
        "\n"
        "    CPU and CUDA torch are the same package: installing one removes the other,\n"
        "    and PyPI's Windows wheels are CPU-only. If you installed a CUDA build\n"
        "    earlier, either a later pip install replaced it, or this is a different\n"
        "    environment than the one you installed into (check the path above).\n"
        "\n"
        "    Blackwell (RTX 5090, sm_120) needs cu128 — torch 2.7.0 or newer:\n"
        "      pip install --force-reinstall torch --index-url https://download.pytorch.org/whl/cu128\n"
        "\n"
        "    Then re-run. To proceed on CPU anyway (hours, not minutes): --allow-cpu\n")


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
    # CUDA by default. The embed stage is the only slow stage and the box has a 5090;
    # opting IN to the GPU every time is how a run silently costs hours.
    ap.add_argument("--device", default="cuda",
                    help="embed device (default: cuda). Absence of CUDA is a hard stop "
                         "unless --allow-cpu.")
    ap.add_argument("--allow-cpu", action="store_true",
                    help="proceed on CPU when CUDA is unavailable (hours, not minutes)")
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

    # Check the GPU before spending any time on stages 1-4.
    if not a.dry_run:
        _preflight_device(a.device, a.allow_cpu)

    print(f"assets for {pkg.name}   dict_sig={dict_sig}   suite={sorted(enabled)}"
          + ("   [DRY RUN]" if a.dry_run else ""))
    print(f"  browser_out={browser_out}   stamp={stamp_release}/{stamp_status}")
    print(f"{'#':>2}  {'stage':<20}{'state':<26}action")
    print("-" * 70)
    for st in _stages(pkg, a.device, browser_out, stamp_release, stamp_status):
        # STAGE 9 (registry) IS EXEMPT FROM FILTERING. It is how the manifest learns
        # what a run changed; skipping it under --only/--from is how the manifest
        # lied twice (epa.present=false while epa.bin shipped; vfacets invisible).
        # It is cheap, idempotent, and reads only the artifact -- always re-derive.
        _is_registry = (st["n"] == 9)
        if only is not None and st["n"] not in only and not _is_registry:
            continue
        if a.frm is not None and st["n"] < a.frm and not _is_registry:
            continue
        asset = STAGE_ASSET.get(st["n"])
        if asset is not None and asset not in enabled:
            print(f"{st['n']:>2}  {st['name']:<20}{('off (not in suite)'):<26}skip")
            continue
        # GATES ALWAYS RUN. They produce no output files, so _stale() judged them purely
        # by the ledger: on any re-run against an unchanged dictionary they were reported
        # "up to date" and skipped. A verification that did not execute printed the same
        # reassuring line as one that passed — the third way this cascade said yes
        # without checking (see stage 11's --db, and the failure-continues bug above).
        if st.get("gate"):
            stale, why = True, "gate (always runs)"
        else:
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
        # STOP ON FAILURE. Later stages consume earlier outputs: stage 8 builds
        # neighbours.bin from the stage-5 index, so once 5 dies every stage after it is
        # either doomed or producing an asset that does not match its siblings. The old
        # behaviour printed each failure and ran on to a normal-looking summary, which
        # is how elo-browser-v01b came out of a "successful" build with no neighbours.bin
        # and a browser that could not load it.
        if res.startswith("FAILED"):
            if not a.dry_run:
                _write_ledger(pkg, ledger)
            sys.exit(f"\n[abort] stage {st['n']} ({st['name']}) {res}. Stages after it "
                     f"depend on its output — fix this before re-running. The ledger is "
                     f"written, so a re-run resumes from here.")
    if not a.dry_run:
        _write_ledger(pkg, ledger)
        print(f"\nledger -> {lpath.name}")


if __name__ == "__main__":
    main()
