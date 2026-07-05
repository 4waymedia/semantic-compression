"""
facet_accuracy.py -- measure whether facet/meta assignments are *correct*.

P2 of docs/compression/spec-facet-accuracy.md. The structural gates
(verify_facets.py / test_facets.py) prove facets are complete, in-enum,
deterministic, and fingerprinted; this adds the missing axis -- accuracy -- by
scoring the build's predicted facets (from meta.db) against the human gold set.

Pure stdlib (sqlite3 + csv). No lmdb, no model, no dictionary needed -- it reads
the already-built meta.db (SQLite) and data/facet_gold.tsv. Deterministic; writes
only its outputs (misses csv + report), never the dictionary.

    python facet_accuracy.py [META_DB]
        --gold    data/facet_gold.tsv
        --misses  data/facet_misses.csv        (the override-priority queue, U4)
        --report  facet_accuracy_report.md
    python facet_accuracy.py --selftest         (synthetic, no real build needed)

Predicted comes from meta.db so the score reflects the exact build. Gold is the
rubric labels. Each dimension is scored independently (spec §6):
  M1 per-dimension accuracy (bucket / utility / abstraction / temporality / scope)
  M2 logic-cue set F1 (precision+recall over the 15 composable cues)
  M3 confusion matrix per dimension (surfaces the TOPIC<->CONCEPT weak spot)
  M4 gold coverage (% of gold present in this build)
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

# --- canonical enums (spec §3) -------------------------------------------------
BUCKETS = ("UNKNOWN", "TOPIC", "METHOD", "CONCEPT", "RELATION", "STRUCTURAL")
UTILITIES = ("CONTENT", "FUNCTION", "STRUCTURAL", "FILLER")
LOGIC_CUES = ("CAUSE", "CONDITION", "INFERENCE", "EVIDENCE_CUE", "CONTRAST",
              "CONCESSION", "NEGATION", "QUANTIFIER", "MODAL", "QUESTION",
              "TEMPORAL", "COMPARISON", "DEFINITION_CUE", "CONJUNCTION", "CLAIM_CUE")

SCALAR_DIMS = ("kind", "bucket", "utility", "abstraction", "temporality", "scope")
SET_DIMS = ("logic_cues", "causality")
ALL_DIMS = SCALAR_DIMS + SET_DIMS

# desired results / release targets (spec §5)
# RECALIBRATED 2026-07-05 (gold v2 promotion, baseline .923/.761/.833), then
# RATCHETED same day after the 89-miss triage landed (F1-F4/F6 fixes + 6 gold
# fixes + stale-override removal): measured 1.000/1.000/1.000, abstraction .960.
# Targets restored to the spec's aspirational values. Honest caveat: the gold
# set was the calibration surface for the triage, so perfect scores here mean
# "fit," not "generalization" -- the 577-row review queue graduating into gold
# is what tests generalization.
TARGETS = {
    "utility": 0.95,                 # over all gold
    "bucket_content": 0.85,          # gold utility == CONTENT
    "cue_f1_function": 0.90,         # gold utility == FUNCTION
}
# abstraction is a System-2 / meta_layer=2 property: "concrete" has NO surface signal
# (it needs the concreteness substrate), so it is UNREACHABLE at layer 1 and must not
# gate a layer-1 build. CONDITIONAL PROMOTION (2026-07-05, cross-session handoff):
# the gate reads meta_layer from meta_info — ACTIVE target on meta_layer>=2 builds
# (passes 0.92 via Brysbaert), still pending/informational on layer-1-only builds
# (structurally 0.28 there). See DEVELOPMENT_LIST L7 + spec §5.
PENDING_L2 = {
    "abstraction": 0.90,             # over gold rows with an abstraction label
    # (ratcheted 0.80 -> 0.90 on 2026-07-05: measured .960 post-triage)
}

_EMPTY = {None, "", "-", "null", "none", "[]"}


# --- normalization -------------------------------------------------------------

def _scalar(v):
    """Normalize a scalar cell to a value or None (empty sentinels -> None)."""
    if v is None:
        return None
    s = str(v).strip()
    return None if s.lower() in _EMPTY else s


def _set(v):
    """Normalize a multi-value cell (gold 'A,B' or meta JSON '["A","B"]') to a set."""
    if v is None:
        return frozenset()
    s = str(v).strip()
    if s.lower() in _EMPTY:
        return frozenset()
    if s.startswith("["):
        try:
            return frozenset(str(x).strip().upper() for x in json.loads(s) if str(x).strip())
        except Exception:
            pass
    return frozenset(p.strip().upper() for p in s.split(",") if p.strip())


# --- loaders -------------------------------------------------------------------

def load_gold(path: Path) -> dict:
    """{surface: record} from the gold TSV (comments and blank lines skipped)."""
    gold = {}
    with open(path, encoding="utf-8") as f:
        rows = [ln for ln in f if ln.strip() and not ln.startswith("#")]
    # QUOTE_NONE: surfaces are literal (the gold set contains bare " and "")
    # -- default csv quoting would swallow the rest of the file as one field.
    reader = csv.DictReader(rows, delimiter="\t", quoting=csv.QUOTE_NONE)
    for r in reader:
        surf = (r.get("surface") or "").strip()
        if not surf:
            continue
        gold[surf] = _record(r)
    return gold


def load_predicted(meta_db: Path, surfaces=None) -> dict:
    """{surface: record} from meta.db. If `surfaces` given, fetch only those."""
    con = sqlite3.connect(f"file:{meta_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cols = "surface,kind,bucket,utility,logic_cues,abstraction,causality,temporality,scope"
    pred = {}
    try:
        has = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        if not has:
            size = Path(meta_db).stat().st_size
            raise RuntimeError(
                f"'{meta_db}' has no 'meta' table (file is {size} bytes). "
                f"It looks unbuilt/empty — rebuild it:\n"
                f"    python meta_builder.py {Path(meta_db).parent}")
        if surfaces:
            qs = ",".join("?" * len(surfaces))
            cur = con.execute(f"SELECT {cols} FROM meta WHERE surface IN ({qs})", list(surfaces))
        else:
            cur = con.execute(f"SELECT {cols} FROM meta")
        for r in cur:
            pred[r["surface"]] = _record(dict(r))
    finally:
        con.close()
    return pred


def _record(r: dict) -> dict:
    rec = {d: _scalar(r.get(d)) for d in SCALAR_DIMS}
    for d in SET_DIMS:
        rec[d] = _set(r.get(d))
    return rec


def load_meta_layer(meta_db: Path) -> int:
    """meta_layer from meta_info (1 if the table/key is absent — layer-1 build)."""
    con = sqlite3.connect(f"file:{meta_db}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT value FROM meta_info WHERE key='meta_layer'").fetchone()
        return int(row[0]) if row else 1
    except (sqlite3.OperationalError, ValueError):
        return 1
    finally:
        con.close()


# --- scoring -------------------------------------------------------------------

def cue_f1(pairs):
    """Micro precision/recall/F1 over (gold_set, pred_set) pairs."""
    tp = fp = fn = 0
    for g, p in pairs:
        tp += len(g & p)
        fp += len(p - g)
        fn += len(g - p)
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"precision": prec, "recall": rec, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def score(gold: dict, pred: dict, meta_layer: int = 1) -> dict:
    shared = sorted(gold.keys() & pred.keys())
    missing = sorted(gold.keys() - pred.keys())

    acc = {}                       # scalar-dim accuracy over shared
    for d in SCALAR_DIMS:
        hit = sum(1 for s in shared if gold[s][d] == pred[s][d])
        acc[d] = (hit / len(shared)) if shared else 0.0

    # targeted subsets
    content = [s for s in shared if gold[s]["utility"] == "CONTENT"]
    function = [s for s in shared if gold[s]["utility"] == "FUNCTION"]
    labeled_abs = [s for s in shared if gold[s]["abstraction"] is not None]

    bucket_content = (sum(1 for s in content if gold[s]["bucket"] == pred[s]["bucket"])
                      / len(content)) if content else 0.0
    abstraction_acc = (sum(1 for s in labeled_abs if gold[s]["abstraction"] == pred[s]["abstraction"])
                       / len(labeled_abs)) if labeled_abs else 0.0

    cues_all = cue_f1([(gold[s]["logic_cues"], pred[s]["logic_cues"]) for s in shared])
    cues_function = cue_f1([(gold[s]["logic_cues"], pred[s]["logic_cues"]) for s in function])
    causality = cue_f1([(gold[s]["causality"], pred[s]["causality"]) for s in shared])

    # confusion matrices (gold -> predicted) for the scalar dims that matter
    confusion = {}
    for d in ("bucket", "utility", "abstraction"):
        cm = defaultdict(Counter)
        for s in shared:
            cm[gold[s][d] or "-"][pred[s][d] or "-"] += 1
        confusion[d] = {g: dict(c) for g, c in cm.items()}

    # misses (override queue): every shared surface/dim that disagrees
    misses = []
    for s in shared:
        for d in SCALAR_DIMS:
            if gold[s][d] != pred[s][d]:
                misses.append((s, d, gold[s][d] or "-", pred[s][d] or "-"))
        for d in SET_DIMS:
            if gold[s][d] != pred[s][d]:
                misses.append((s, d, ",".join(sorted(gold[s][d])) or "-",
                               ",".join(sorted(pred[s][d])) or "-"))

    targets = {
        "utility": (acc["utility"], TARGETS["utility"]),
        "bucket_content": (bucket_content, TARGETS["bucket_content"]),
        "cue_f1_function": (cues_function["f1"], TARGETS["cue_f1_function"]),
    }
    # conditional promotion: abstraction gates a layer-2 build, stays
    # informational on layer-1 (no concreteness/EPA columns -> 0.28 is honest)
    pending = {}
    ab_entry = (abstraction_acc, PENDING_L2["abstraction"])
    if meta_layer >= 2:
        targets["abstraction"] = ab_entry
    else:
        pending["abstraction"] = ab_entry

    return {
        "meta_layer": meta_layer,
        "n_gold": len(gold), "n_shared": len(shared), "missing": missing,
        "coverage": (len(shared) / len(gold)) if gold else 0.0,
        "accuracy": acc, "bucket_content": bucket_content,
        "abstraction_labeled": abstraction_acc,
        "cues_all": cues_all, "cues_function": cues_function, "causality": causality,
        "confusion": confusion, "misses": misses, "targets": targets, "pending": pending,
        "subset_sizes": {"content": len(content), "function": len(function),
                         "labeled_abstraction": len(labeled_abs)},
    }


# --- reporting -----------------------------------------------------------------

def _fmt_confusion(cm: dict) -> str:
    lines = []
    for g in sorted(cm):
        preds = ", ".join(f"{p}:{n}" for p, n in sorted(cm[g].items(), key=lambda kv: -kv[1]))
        lines.append(f"    {g:<11} -> {preds}")
    return "\n".join(lines)


def render_report(res: dict, meta_db: Path) -> str:
    L = []
    a = L.append
    a(f"# Facet Accuracy Report\n")
    a(f"- build meta.db: `{meta_db}`")
    a(f"- gold rows: {res['n_gold']}  ·  scored (in build): {res['n_shared']}  "
      f"·  coverage: {res['coverage']:.0%}")
    if res["missing"]:
        a(f"- gold surfaces missing from build: {', '.join(res['missing'])}")
    a("\n## Targets (release gate)\n")
    a("| dimension | score | target | pass |")
    a("|---|---:|---:|:--:|")
    for k, (got, tgt) in res["targets"].items():
        a(f"| {k} | {got:.3f} | {tgt:.2f} | {'PASS' if got >= tgt else 'FAIL'} |")
    if res.get("pending"):
        a("\n## Pending — System-2 (meta_layer=2), NOT gated at layer 1\n")
        a(f"> abstraction needs the S2 columns (this build: meta_layer="
          f"{res.get('meta_layer', 1)}). Informational until the layer-2 pass runs.\n")
        a("| dimension | score | L2 target | status |")
        a("|---|---:|---:|:--:|")
        for k, (got, tgt) in res["pending"].items():
            a(f"| {k} | {got:.3f} | {tgt:.2f} | {'ok' if got >= tgt else 'pending L7'} |")
    else:
        a(f"\n> abstraction gate ACTIVE (meta_layer={res.get('meta_layer', 1)}) — "
          f"scored in Targets above. Promoted 2026-07-05.")
    a("\n## Per-dimension accuracy (all scored)\n")
    a("| dimension | accuracy |")
    a("|---|---:|")
    for d in SCALAR_DIMS:
        a(f"| {d} | {res['accuracy'][d]:.3f} |")
    a(f"| bucket (content only, n={res['subset_sizes']['content']}) | {res['bucket_content']:.3f} |")
    a(f"| abstraction (labeled only, n={res['subset_sizes']['labeled_abstraction']}) | {res['abstraction_labeled']:.3f} |")
    a("\n## Logic-cue set F1\n")
    for name, c in (("all", res["cues_all"]),
                    (f"function subset (n={res['subset_sizes']['function']})", res["cues_function"]),
                    ("causality", res["causality"])):
        a(f"- {name}: F1={c['f1']:.3f} (P={c['precision']:.3f} R={c['recall']:.3f}; "
          f"tp={c['tp']} fp={c['fp']} fn={c['fn']})")
    a("\n## Confusion (gold -> predicted)\n")
    for d in ("bucket", "utility", "abstraction"):
        a(f"  {d}:")
        a(_fmt_confusion(res["confusion"][d]))
    a(f"\n## Misses: {len(res['misses'])} (written to the misses CSV -> override queue)\n")
    return "\n".join(L) + "\n"


def write_misses(path: Path, misses: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["surface", "dimension", "gold", "predicted"])
        w.writerows(sorted(misses))


# --- selftest ------------------------------------------------------------------

def _selftest() -> int:
    """Synthetic meta.db + gold; verify scoring end-to-end (no real build needed)."""
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "meta.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE meta (surface TEXT PRIMARY KEY, kind, bucket, utility, "
                "logic_cues, abstraction, causality, temporality, scope)")
    con.executemany("INSERT INTO meta VALUES (?,?,?,?,?,?,?,?,?)", [
        # surface, kind, bucket, utility, logic_cues(JSON), abstraction, causality, temporality, scope
        ("rabbit", "word", "TOPIC", "CONTENT", None, "concrete", None, None, None),
        ("freedom", "word", "TOPIC", "CONTENT", None, "abstract", None, None, None),   # bucket miss (CONCEPT)
        ("because", "word", "RELATION", "FUNCTION", '["CAUSE"]', None, '["CAUSE"]', None, None),  # cue miss (missing EVIDENCE_CUE)
        ("when", "word", "RELATION", "FUNCTION", '["CONDITION","QUESTION","TEMPORAL"]', None, '["CONDITION"]', "temporal", None),
        ("um", "word", "UNKNOWN", "FILLER", None, None, None, None, None),
    ])
    con.commit(); con.close()

    gold = {
        "rabbit": _record({"surface": "rabbit", "kind": "word", "bucket": "TOPIC", "utility": "CONTENT",
                           "logic_cues": "-", "abstraction": "concrete", "causality": "-", "temporality": "-", "scope": "-"}),
        "freedom": _record({"surface": "freedom", "kind": "word", "bucket": "CONCEPT", "utility": "CONTENT",
                            "logic_cues": "-", "abstraction": "abstract", "causality": "-", "temporality": "-", "scope": "-"}),
        "because": _record({"surface": "because", "kind": "word", "bucket": "RELATION", "utility": "FUNCTION",
                            "logic_cues": "CAUSE,EVIDENCE_CUE", "abstraction": "-", "causality": "CAUSE,EVIDENCE_CUE", "temporality": "-", "scope": "-"}),
        "when": _record({"surface": "when", "kind": "word", "bucket": "RELATION", "utility": "FUNCTION",
                         "logic_cues": "CONDITION,QUESTION,TEMPORAL", "abstraction": "-", "causality": "CONDITION", "temporality": "temporal", "scope": "-"}),
        "um": _record({"surface": "um", "kind": "word", "bucket": "UNKNOWN", "utility": "FILLER",
                       "logic_cues": "-", "abstraction": "-", "causality": "-", "temporality": "-", "scope": "-"}),
        "ghost": _record({"surface": "ghost", "kind": "word", "bucket": "TOPIC", "utility": "CONTENT",
                          "logic_cues": "-", "abstraction": "concrete", "causality": "-", "temporality": "-", "scope": "-"}),  # not in build
    }
    pred = load_predicted(db, surfaces=list(gold))
    res = score(gold, pred)

    ok = True
    def check(name, got, exp):
        nonlocal ok
        good = abs(got - exp) < 1e-9
        ok = ok and good
        print(f"  [{'ok' if good else 'XX'}] {name}: {got:.4f} (expected {exp})")

    check("coverage (5/6)", res["coverage"], 5/6)
    check("utility acc (all correct)", res["accuracy"]["utility"], 1.0)
    check("bucket acc (freedom wrong -> 4/5)", res["accuracy"]["bucket"], 4/5)
    check("bucket_content (rabbit ok, freedom wrong -> 1/2)", res["bucket_content"], 0.5)
    # cue F1 over function subset: because gold{CAUSE,EVIDENCE_CUE} pred{CAUSE}; when gold{COND,QUES,TEMP} pred{COND,QUES,TEMP}
    # tp = 1(cause)+3(when) = 4 ; fp = 0 ; fn = 1(evidence_cue)
    check("cue function precision", res["cues_function"]["precision"], 1.0)
    check("cue function recall (4/5)", res["cues_function"]["recall"], 4/5)
    # abstraction labeled: rabbit concrete ok, freedom abstract ok -> 2/2
    check("abstraction labeled (2/2)", res["abstraction_labeled"], 1.0)
    # misses: freedom/bucket + because/logic_cues + because/causality = 3
    n_expected_misses = 3
    print(f"  [{'ok' if len(res['misses'])==n_expected_misses else 'XX'}] misses count: "
          f"{len(res['misses'])} (expected {n_expected_misses}) -> {sorted((m[0],m[1]) for m in res['misses'])}")
    ok = ok and len(res["misses"]) == n_expected_misses

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# --- main ----------------------------------------------------------------------

def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Facet accuracy harness (spec-facet-accuracy.md §5).")
    ap.add_argument("meta_db", nargs="?",
                    default=str(here / "db/builds/general_v0.4_char4/meta.db"),
                    help="path to a build's meta.db (SQLite)")
    ap.add_argument("--gold", default=str(here / "data/facet_gold.tsv"))
    ap.add_argument("--misses", default=str(here / "data/facet_misses.csv"))
    ap.add_argument("--report", default=str(here / "facet_accuracy_report.md"))
    ap.add_argument("--selftest", action="store_true", help="run synthetic self-test and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    gold_path, meta_db = Path(args.gold), Path(args.meta_db)
    if not gold_path.exists():
        print(f"gold set not found: {gold_path}", file=sys.stderr); return 2
    if not meta_db.exists():
        print(f"meta.db not found: {meta_db}", file=sys.stderr); return 2

    gold = load_gold(gold_path)
    try:
        pred = load_predicted(meta_db, surfaces=list(gold))
    except (RuntimeError, sqlite3.OperationalError) as e:
        print(f"ERROR reading meta.db: {e}", file=sys.stderr)
        return 2
    res = score(gold, pred, meta_layer=load_meta_layer(meta_db))

    write_misses(Path(args.misses), res["misses"])
    report = render_report(res, meta_db)
    Path(args.report).write_text(report, encoding="utf-8")
    print(report)
    print(f"misses -> {args.misses}   report -> {args.report}")

    # exit non-zero if any release target fails (so it can gate a build)
    failed = [k for k, (got, tgt) in res["targets"].items() if got < tgt]
    if failed:
        print("TARGETS FAILED:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
