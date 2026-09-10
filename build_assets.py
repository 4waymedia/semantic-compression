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
import datetime as _dt
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

# THE EXPORTERS LIVE HERE (moved 2026-09-10, integration lane's finding).
#
# They were in `ELO-Browser/tools/` and run as stages 7, 8 and 16 of THIS cascade. The
# dictionary lane owned the assets; the browser lane owned the code that produced them.
# Three consequences, all of which we paid:
#
#   1. A clone of `semantic_compression` alone could not build its own assets.
#   2. A consumer lane could change an asset's wire format by editing its own tools/.
#   3. The PRODUCER (browser tools) and the PUBLISHER (publish_dictionary.py) sat in
#      different lanes with no shared declaration, so an asset added to one was
#      invisible to the other BY CONSTRUCTION. That is the mechanical cause of the nine
#      asset lists and of `wordclass` having an exporter for weeks while
#      `BUNDLE_CHANNELS` never heard of it.
#
# `asset_registry.py` cannot govern code it does not contain, so the registry work is
# only real once the exporters are here. Ownership was already honoured in who RUNS
# them; this fixes where they LIVE.
EXPORTERS = SC
BROWSER_TOOLS = EXPORTERS        # deprecated alias; remove once no branch references it
# O3: browser assets land in a PER-DICTIONARY subdir so shipped products never
# collide. The runtime selects a product dir; the default is keyed by build name.
# Layer-4 evidence for the wordclass stage. Repo-relative so it resolves identically
# on every machine; the stage refuses rather than silently using the 427k sample.
CORPUS_TEXT = ROOT / "Resources" / "books"

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
               13: "vfacets",
               14: "census",
               15: "wordclass",
               # 16 gates on the same suite asset as 15: a build that declares
               # `wordclass` builds it AND ships it. Splitting them would recreate the
               # exact gap this stage closes -- a channel built and never exported.
               16: "wordclass"}


def _dict_fingerprint(lmdb_path: Path) -> str | None:
    """The ID SPACE fingerprint: sha256 over sorted (surface, id) pairs.

    Deliberately NOT a hash of the file. An asset write changes the file and must not
    change this -- that is the whole invariant. An `.elo` binds to this value, because
    the ids it contains are all decode needs; every asset is additive on top."""
    try:
        import lmdb as _l
        env = _l.open(str(lmdb_path), readonly=True, max_dbs=32, lock=False)
    except Exception:
        return None
    try:
        fwd = env.open_db(b"forward", create=False)
        h = hashlib.sha256()
        with env.begin() as t:
            for k, v in t.cursor(db=fwd):
                h.update(bytes(k)); h.update(b"\t"); h.update(bytes(v)); h.update(b"\n")
        return h.hexdigest()
    finally:
        env.close()


def _sig(path: Path) -> str:
    """Change-signature for the DICTIONARY, not the file.

    2026-08-26 audit (class 3, SELF-INVALIDATION): this hashed size+mtime of
    data.mdb -- but stages 1/3/10/13 WRITE INTO the LMDB (facets/epa/stamp/
    vfacets are additive sub-dbs), so every cascade run changed the sig and the
    next run considered ALL stages stale, GPU embed included. Measured on v04:
    ledger sigs b3719eae vs live 0a80c9ae -> 13/13 spuriously stale.

    Staleness is supposed to track the invalidation rule the docs state:
    'every dictionary REBUILD invalidates derived assets; facets/meta are
    additive and never alter forward/reverse.' So the signature IS the
    dictionary_fingerprint stamped in meta (pure surface<->id content). The
    additive stages can no longer invalidate anything; a core rebuild (new
    forward) still invalidates everything. Falls back to size+mtime for
    non-LMDB paths or an unstamped meta."""
    if path.is_dir() and (path / "data.mdb").exists():
        try:
            import lmdb  # noqa: PLC0415
            env = lmdb.open(str(path), readonly=True, lock=False, max_dbs=16)
            try:
                meta = env.open_db(b"meta", create=False)
                with env.begin() as txn:
                    fp = txn.get(b"dictionary_fingerprint", db=meta)
                if fp:
                    return fp.decode()[:16]
            finally:
                env.close()
        except Exception:
            pass
        p = path / "data.mdb"
    else:
        p = path
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

    # ---- WHICH PROFILE THE BROWSER GETS -----------------------------------
    # `reference` = the WHOLE dictionary. Changed from `full` 2026-08-11 (Paul).
    #
    # profile-cuts.json holds five cuts. FOUR OF THEM ARE LLM VOCABULARY SIZES:
    #
    #     tiny 32,768 · compact 65,536 · standard 131,072 · full 262,144
    #
    # -- powers of two, minus 256 byte-fallback and 16 special tokens. That
    # arithmetic serves exactly one consumer: a model's embedding table, from
    # the v0.3.0 "5 standardized LLM vocab profiles" work (docs/v1/profiles.md).
    # `reference` is the only cut that is not an LLM size; it is all content.
    #
    # THE BROWSER IS NOT AN LLM. It has no embedding table and no reason to sit
    # on a power of two -- it borrowed `--cut full` because the mechanism was
    # here, and the rationale did not travel with it. The name is the trap:
    # `full` means "full LLM profile", and reads as "full dictionary".
    #
    # MEASURED COST OF THE OLD DEFAULT (elo-browser-v01c, 2026-08-11):
    #     dictionary       437,995 entries
    #     browser vocab    261,872 entries
    #     unaddressable    176,123  -- 40% of the dictionary
    #
    # And the cut is BY FREQUENCY RANK, which is correct for an LLM and exactly
    # backwards here: a fact is specific precisely when its terms are rare.
    # `9070`, `7600`, `nvme` are all in the dictionary and were all past the cut,
    # so the browser had no id to hold "RX 9070 XT" with. Summaries could not
    # state the article's facts because the tokens were unaddressable -- not
    # because selection declined to choose them.
    #
    # Note the enrichment ordering this also fixes: facets/epa/vfacets/neighbours
    # are built over ALL 437,995 entries in LMDB and were then truncated to
    # 261,872 at export. We paid for the tail and shipped 60% of it.
    #
    # SAFETY, checked before the change: no 18-bit assumption exists in the Rust
    # port. neighbours.bin uses u32 counts and u32 neighbour indices; facets.bin
    # and epa.bin are parallel arrays sized from the vocab. 438k fits in u32 with
    # room. Cost is roughly +67% on the .bin channel set (~11MB -> ~18MB).
    # "reference" = the WHOLE dictionary (437,995): the browser ships every entry,
    # not the LLM `full` embedding cut (261,872). Paul's intent all along -- the
    # first attempt failed only because export_browser_vocab lacked the choice
    # (added 2026-08-10). Profile cuts remain an LLM-budget concept; the browser
    # is not budget-bound, and every entry it lacks costs OOV bytes on the wire.
    # A cut change changes n: stages 5-8 must re-run TOGETHER after changing this
    # (the embed limit below follows it), and channels from different cuts must
    # never mix -- G1/G2 enforce that at publish.
    BROWSER_CUT = "reference"

    # Embed scope = the browser vocab cut, so the 768-d index covers exactly the
    # vocab n-range. profile-cuts.json is emitted by the CORE build (before
    # stage 5), so no ordering inversion. MUST use the same cut as stage 6 --
    # a denotative index over a different n-range than the vocab is silently
    # misaligned, which is the "one vocabulary, one n" rule (spec-asset-pipeline §1).
    limit = []
    pc = pkg / "profile-cuts.json"
    if pc.exists():
        try:
            cs = json.loads(pc.read_text(encoding="utf-8"))[BROWSER_CUT]["content_size"]
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
             # --epa-db points at the build's OWN LMDB (2026-08-27): stage 3
             # (epa_match.py) has already written the id-keyed b'epa' channel
             # by the time this stage runs, and the builder sniffs the key
             # scheme -- id-keyed primary, the 67,936-term en| surface
             # substrate as blend fallback. The old value here (the external
             # substrate alone) was the root cause of the polarity hole:
             # 437,995 surfaces joined against 67,936 terms while 236,645
             # id-keyed ratings sat unused in the same LMDB. Measured on v04:
             # polarity non-neutral 29,392 -> 105,191.
             argv=["--db", str(lmdb), "--epa-db", str(lmdb)],
             out=[pkg / "vfacets_stats.json"]),
        dict(n=5, name="denotative", script=SC / "denotative_index.py", cwd=SC,
             dep="sentence_transformers", multi=[
                 ["embed", "--meta", str(pkg / "meta.db"), "--out", str(pkg)] + dev + limit,
                 ["finalize", "--meta", str(pkg / "meta.db"), "--out", str(pkg)]],
             out=[pkg / "dictionary.denotative.json"]),
        dict(n=6, name="browser-vocab", script=SC / "export_browser_vocab.py", cwd=SC,
             dep=None, argv=["--build", str(pkg), "--cut", BROWSER_CUT], out=[vocab]),
        # --out-root, not --out: the exporter appends the build name itself, so the
        # versioned folder is guaranteed rather than assembled by each caller.
        dict(n=7, name="browser-epa+facets", script=EXPORTERS / "export_browser_assets.py",
             cwd=ROOT, dep="lmdb",
             argv=["--build", str(pkg), "--out-root", str(browser_out.parent)],
             out=[browser_out / "epa.bin", browser_out / "facets.bin",
                  browser_out / "assets.meta.json",
                  browser_out / f"{pkg.name}.browser.json"]),
        dict(n=8, name="browser-neighbours", script=EXPORTERS / "export_neighbours.py",
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
        # STAGE 14 -- COVERAGE CENSUS. gate=True, so it ALWAYS runs.
        #
        # A census is a claim about THIS fingerprint. Every channel above is
        # re-derived per build, so a census that is merely "up to date" by ledger
        # is a census describing a dictionary that no longer exists -- the same
        # defect class as a restated fingerprint, which is the mistake this repo
        # keeps catching. Cheap (~3.5s) and always current beats cached and wrong.
        #
        # It is deliberately LAST: it measures the finished package, including the
        # channels stages 1/3/13 wrote. Running it earlier would census a half-built
        # artifact and report the gaps as findings.
        # STAGE 15 -- WORDCLASS. Always declared, never inherited.
        #
        # This channel was built BY HAND for its whole life, which is two hazards at
        # once: a rebuild that forgets it leaves the channel stale while the LMDB still
        # reports it PRESENT (so `dictionary_info` says yes and the data is a
        # generation behind), and a run that omits `--corpus-text` silently falls back
        # to a 427k-token sample. That sample is not a smaller version of the same
        # thing -- layer 4's determiner counts are the DECIDING evidence for noun vs
        # verb, so without the books corpus `dog`, `stone` and `water` go back to
        # reading VERB. A stage that can be run wrong by omission belongs in the
        # cascade with its arguments fixed.
        #
        # CORPUS_TEXT is resolved repo-relative so it means the same thing on every
        # machine, and the stage FAILS rather than degrading if it is missing -- see
        # the guard below.
        dict(n=15, name="wordclass", script=SC / "wordclass_builder.py", cwd=SC,
             dep="lmdb",
             argv=["--db", str(lmdb),
                   "--corpus", str(SC / "data" / "word_frequencies.txt"),
                   "--corpus-text", str(CORPUS_TEXT)],
             out=[pkg / "wordclass_stats.json"]),
        # STAGE 16 -- EXPORT WORDCLASS TO THE BUNDLE.
        #
        # Stage 15 builds the channel into the LMDB; nothing carried it any further.
        # From 2026-08-28 to 2026-09-10 `wordclass` was built by every run, locked at
        # format v3, stamped in meta, measured by the census, and consumed by the
        # Verbalizer through a direct LMDB open -- because there was no exporter and no
        # bundle channel. It passed every publish gate by not being in BUNDLE_CHANNELS.
        #
        # It runs AFTER stage 15 (needs the sub-db) and after stage 6 (needs the vocab
        # json that defines n). Position in this list is execution order; n=16 is an
        # append-only id, like 13/14/15 before it.
        dict(n=16, name="wordclass-export", script=EXPORTERS / "export_wordclass.py",
             cwd=ROOT, dep="lmdb",
             argv=["--build", str(pkg), "--out", str(browser_out)],
             out=[browser_out / "wordclass.bin",
                  browser_out / "wordclass.names.json"]),
        dict(n=14, name="census", script=SC / "coverage_census.py", cwd=SC,
             dep="lmdb", argv=["--db", str(lmdb)],
             out=[pkg / "coverage_census.json"], gate=True),
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
    # ARGV IS PART OF THE INPUT. A stage whose ARGUMENTS changed is stale even
    # when its inputs and script have not.
    #
    # Added 2026-08-11, because this is the mechanism that hid a 40% loss for
    # months. Stage 6 ran `--cut full` (an LLM vocab profile, 2^18) instead of
    # `--cut reference` (the whole dictionary), so 176,123 of 437,995 entries
    # were unaddressable by the browser. Changing the flag did NOT mark the
    # stage stale -- the dictionary was unchanged and the script untouched, so
    # the cascade reported "up to date" and rebuilt nothing. A config change
    # that the build cannot see is a config change that silently does not apply.
    recorded = rec.get("argv_sig")
    if recorded is None:
        # Pre-2026-08-11 ledgers did not record arguments, so we cannot say what
        # this stage was run with. UNKNOWN PROVENANCE IS STALE -- the same rule
        # applied to every absent channel today: refuse to claim currency you
        # cannot establish. Costs one rebuild per build, once.
        return True, "arguments unknown (pre-argv ledger)"
    if recorded != _argv_sig(stage):
        return True, "arguments changed"
    return False, "up to date"


def _argv_sig(stage) -> str:
    """Signature of a stage's ARGUMENTS. Absolute paths are stripped so moving
    the checkout does not read as a config change -- only the flags matter."""
    parts = [str(a) for a in stage.get("argv") or []]
    for m in stage.get("multi") or []:
        parts += [str(a) for a in m]
    norm = [p for p in parts if not Path(p).is_absolute()]
    return hashlib.sha256("\x1f".join(norm).encode()).hexdigest()[:16]


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
                               # what the stage was RUN WITH -- see _stale()
                               "argv_sig": _argv_sig(stage),
                               "argv": [str(a) for a in (stage.get("argv") or [])
                                        if not Path(str(a)).is_absolute()],
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
    # --- upgrading an OLDER build with a NEWER asset (2026-09-10, Paul) ---------
    #
    # "As we expand or add on these assets, older dictionaries can be rebuilt with them."
    # The re-run machinery already existed (--only/--from/--force + the staleness
    # ledger). What did not was the question that comes FIRST -- *which assets is this
    # build missing?* -- and the ability to answer it, because a build's `suite.enabled`
    # is recorded at build time and is authoritative. elo-browser-v04 was declared
    # `full` on 2026-08-13, before `wordclass` existed, so `full` for that build means
    # eight assets forever and no amount of --force adds a ninth.
    #
    # That recording is CORRECT -- the declared suite is part of a build's identity, and
    # silently widening it on re-run is how an artifact stops matching its own manifest.
    # So widening is an explicit act with an explicit flag.
    ap.add_argument("--audit", action="store_true",
                    help="report which registry assets this build lacks, and the "
                         "command that would add them. Reads only; changes nothing.")
    ap.add_argument("--add-asset", default=None, metavar="NAME[,NAME...]",
                    help="widen this build's RECORDED suite to include NAME, then run "
                         "its stages. The declared suite is part of the build's "
                         "identity, so this is deliberate and logged -- not implied by "
                         "--force.")
    ap.add_argument("--bump-revision", default=None, metavar="REASON",
                    help="increment package_revision for a CONTENT change that adds no "
                         "asset (a corrected channel, a re-derivation). package_revision "
                         "means 'the assets INCLUDING their content' -- the facets/utility "
                         "fix changes 14,394 records and adds nothing, and a consumer "
                         "caching verdicts under revision 1 must be able to see that.")
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
    _fp_before = None
    if a.bump_revision:
        # A CONTENT revision. No asset is added; existing channels are re-derived.
        # Same identity split as --add-asset: the id space must not move, the asset
        # content may, and the revision is what tells the two apart downstream.
        rev = int(man.get("package_revision", 1)) + 1
        man["package_revision"] = rev
        man["bundle_id"] = pkg.name if rev <= 1 else f"{pkg.name}r{rev}"
        # Same entry shape as --add-asset, so one revision is one entry however it was
        # produced. Run BOTH flags in one invocation and the addition attaches to this
        # entry rather than opening a second -- see the note in the --add-asset block.
        man.setdefault("revision_log", []).append({
            "revision": rev,
            "content_change": a.bump_revision,
            "utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "id_space_unchanged": "asserted after the run",
        })
        if not a.dry_run:
            mpath.write_text(json.dumps(man, indent=2), encoding="utf-8")
        print(f"package_revision {rev - 1} -> {rev}   content: {a.bump_revision}")
        print(f"  publishes as {pkg.name}r{rev}   (id space asserted unchanged; "
              f"no .elo file is invalidated)"
              + ("   [DRY RUN -- manifest not written]" if a.dry_run else ""))
        _fp_before = _dict_fingerprint(pkg / "dictionary.lmdb")
    if a.audit or a.add_asset:
        import lmdb as _lmdb
        from asset_registry import ASSETS as _AR, BY_NAME as _BN, missing_from
        _e = _lmdb.open(str(pkg / "dictionary.lmdb"), readonly=True, max_dbs=32,
                        lock=False)
        with _e.begin() as _t:
            _present = {bytes(k).decode() for k, _ in _t.cursor()}
        _e.close()
        _rec = set(rec_suite.get("enabled") or ())
        _lacks = missing_from(_present)
        print(f"audit {pkg.name}")
        print(f"  recorded suite : {', '.join(sorted(_rec)) or '(none)'}")
        print(f"  sub-dbs present: {', '.join(sorted(_present))}")
        for _a in _AR:
            if not _a.in_lmdb or _a.name in ("forward", "reverse"):
                continue
            _has = _a.subdb.decode() in _present
            _why = (f"  -- {_a.unbuilt_reason.split('.')[0]}" if _a.unbuilt_reason else "")
            print(f"    {_a.name:12} {'present' if _has else 'ABSENT ':8}"
                  f"{'declared' if _a.name in _rec else 'not declared':13}{_why}")
        if _lacks:
            print(f"\n  {len(_lacks)} asset(s) missing: {', '.join(_lacks)}")
            print(f"  add them with:\n"
                  f"    python build_assets.py {a.pkg} --add-asset {','.join(_lacks)}")
        else:
            print("\n  this build carries every buildable registry asset")
        if a.audit and not a.add_asset:
            return
    if a.add_asset:
        want = [w.strip() for w in a.add_asset.split(",") if w.strip()]
        from asset_registry import BY_NAME as _BN2
        unknown = [w for w in want if w not in _BN2]
        if unknown:
            sys.exit(f"not in asset_registry: {', '.join(unknown)}. An asset must be "
                     f"declared before it can be built.")
        blocked = [w for w in want if _BN2[w].unbuilt_reason]
        if blocked:
            sys.exit(f"{', '.join(blocked)} has no builder: "
                     f"{_BN2[blocked[0]].unbuilt_reason}")
        # A REBUILD CARRIES A NEW ID (Paul, 2026-09-10). The moment `.elo` files are
        # written and kept, "which dictionary decoded this" has to have one answer.
        #
        # TWO IDENTITIES, AND ONLY ONE OF THEM MAY MOVE HERE:
        #
        #   dictionary_fingerprint  the ID SPACE -- pure (surface, id). THIS is what an
        #                           .elo binds to, because it is all decode needs.
        #                           Adding an asset must NOT move it, and the assertion
        #                           after the run enforces that.
        #   package_revision        the ASSET SET. Increments on every widening, so two
        #                           packages both named elo-browser-v04 are still
        #                           distinguishable, and a consumer can ask for "v04 at
        #                           revision >= 2" when it needs wordclass.
        #
        # Keeping them separate is what makes an in-place asset addition SAFE for
        # existing .elo files rather than a silent break: the ids they contain still
        # resolve, and everything that was added is additive. If the id space ever does
        # move, that is a new dictionary and needs a new build NAME -- not a revision.
        rec_suite = dict(rec_suite)
        rec_suite["enabled"] = sorted(set(rec_suite.get("enabled") or ()) | set(want))
        rec_suite["widened"] = sorted(set(rec_suite.get("widened") or ()) | set(want))
        # ONE ENTRY PER REVISION, recording BOTH kinds of change.
        #
        # `--add-asset` and `--bump-revision` used to write separate entries with
        # separate shapes -- `added` on one, `content_change` on the other -- so a run
        # that did both (r2: added `wordclass` AND re-derived `facets` for 14,394
        # surfaces) logged only the addition. **r2's own revision_log under-describes
        # r2.** Caught by ELO-Browser, and they were right: a revision log that records
        # what was ADDED and not what CHANGED is worse than none, because it reads
        # complete.
        #
        # If a revision is already open in THIS invocation (--bump-revision ran first),
        # extend that entry rather than opening a second -- one revision, one entry.
        rev = int(man.get("package_revision", 1))
        log = man.setdefault("revision_log", [])
        open_entry = log[-1] if (log and _fp_before is not None
                                 and log[-1].get("revision") == rev) else None
        if open_entry is None:
            rev += 1
            man["package_revision"] = rev
            open_entry = {
                "revision": rev,
                "utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                "id_space_unchanged": "asserted after the run",
            }
            log.append(open_entry)
        _bumped_here = (open_entry.get("added") is None)
        open_entry["added"] = sorted(set(open_entry.get("added") or ()) | set(want))
        man["suite"] = rec_suite
        # bundle_id lives in the MANIFEST, derived once. publish_dictionary and
        # dictionary_standard both used to construct this string themselves -- two
        # places building one identity, which is how identities drift.
        man["bundle_id"] = pkg.name if rev <= 1 else f"{pkg.name}r{rev}"
        if not a.dry_run:
            mpath.write_text(json.dumps(man, indent=2), encoding="utf-8")
        print(f"\n  suite widened by {', '.join(want)} -> "
              f"{', '.join(rec_suite['enabled'])}")
        _rev_note = (f"-> {rev}" if _bumped_here
                     else f"{rev} (already open this run)")
        print(f"  package_revision {_rev_note}   bundle_id {man['bundle_id']}"
              + ("   [DRY RUN -- manifest not written]" if a.dry_run else ""))
        _fp_before = _dict_fingerprint(pkg / "dictionary.lmdb")
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
        # Preflight the GPU only when the embed stage (5) can actually run in this
        # invocation -- `--only 2,3,4` on a CPU box must not fail a CUDA check for
        # a stage it will never reach.
        _embed_selected = ((only is None or 5 in only)
                           and (a.frm is None or a.frm <= 5))
        if _embed_selected:
            _preflight_device(a.device, a.allow_cpu)

    print(f"assets for {pkg.name}   dict_sig={dict_sig}   suite={sorted(enabled)}"
          + ("   [DRY RUN]" if a.dry_run else ""))
    print(f"  browser_out={browser_out}   stamp={stamp_release}/{stamp_status}")
    # THE OTHER HALF OF THE REGISTRY CHECK. `publish_dictionary` refuses when a declared
    # asset does not ship; this refuses when a declared asset has no way to be BUILT.
    # `templates` is why: declared in the manifest as an artifact for weeks, carried in
    # every build as an empty sub-db, and produced by no stage anywhere. A registry entry
    # with neither a builder nor a stated reason is a promise nobody keeps.
    # The exporters must be HERE, not in a consumer's tree. Loud and specific rather
    # than a fallback to the old location: a fallback would let the cascade keep running
    # against another lane's copy, which is the condition this move exists to end.
    _missing_exp = [n for n in ("export_browser_assets.py", "export_neighbours.py",
                                "export_wordclass.py") if not (EXPORTERS / n).exists()]
    if _missing_exp:
        raise SystemExit(
            "exporters not found in semantic_compression/: " + ", ".join(_missing_exp)
            + "\n  They moved here on 2026-09-10 so the dictionary no longer builds its "
              "assets from the browser lane's source tree. Run:\n"
            + "".join(f"    git mv ELO-Browser/tools/{n} semantic_compression/{n}\n"
                      for n in _missing_exp))

    from asset_registry import ASSETS as _ASSETS
    _built = {v for v in STAGE_ASSET.values() if v}
    _orphan = [a.name for a in _ASSETS
               if a.in_lmdb and not a.unbuilt_reason and a.name not in _built
               and a.name not in ("forward", "reverse")]
    if _orphan:
        raise SystemExit(
            f"asset_registry declares {', '.join(_orphan)} but no stage builds it. "
            f"Add a stage (and its STAGE_ASSET entry), or record `unbuilt_reason` on "
            f"the registry entry so the gap is visible instead of silent.")

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

    # THE INVARIANT THAT MAKES AN IN-PLACE ASSET ADDITION SAFE.
    #
    # Adding an asset re-derives channels inside the same LMDB under the same build
    # name. That is only legitimate while the ID SPACE is untouched, because an `.elo`
    # written yesterday holds ids and nothing else -- if (surface, id) moved, every
    # stored file silently decodes to different words, and the build name would still
    # say elo-browser-v04.
    #
    # So: assert it, do not assume it. If this ever fires, the correct response is a NEW
    # BUILD NAME, not a revision bump -- a moved id space is a different dictionary
    # wearing the same label, which is the one thing no consumer can detect on its own.
    if (a.add_asset or a.bump_revision) and not a.dry_run and _fp_before:
        _fp_after = _dict_fingerprint(pkg / "dictionary.lmdb")
        if _fp_after != _fp_before:
            sys.exit(
                f"\n[STOP] the ID SPACE MOVED during an asset addition.\n"
                f"  before {_fp_before[:16]}\n  after  {_fp_after[:16] if _fp_after else '?'}\n"
                f"  Every .elo encoded against {pkg.name} now decodes to different "
                f"surfaces. An asset addition must be ADDITIVE. This is a new "
                f"dictionary and needs a new BUILD NAME, not a package_revision.")
        print(f"id space unchanged  {_fp_before[:16]}  "
              f"(package_revision {man.get('package_revision')}; .elo files encoded "
              f"against this build are unaffected)")


if __name__ == "__main__":
    main()
