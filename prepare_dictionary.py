#!/usr/bin/env python3
"""
prepare_dictionary.py -- guided, step-by-step dictionary build.

A clean front door over `build_from_spec`. It walks the canonical sequence:

    1 PREPARE  scaffold builds/<name>.yaml from your choices
    2 BUILD    run build_from_spec -> db/builds/<name>/  (optional: --build)
    3 VERIFY   verify_lossless + verify_facets           (optional: --verify)
    4 REPORT   print entries, fingerprint, profile cuts, and the next commands

It does NOT replace the spec/builder -- it scaffolds the spec and orchestrates the
documented steps so nobody has to remember them. See HOW-TO-CREATE-A-DICTIONARY.md.

Examples
--------
    # write the spec only (review before building):
    python -m semantic_compression.prepare_dictionary \
        --name my-dict --size char-4 --sources transcripts,books --facets

    # scaffold + build + verify:
    python -m semantic_compression.prepare_dictionary \
        --name elo-browser-v01 --size char-4 --sources transcripts,books,web \
        --facets --build --verify

    # answer prompts interactively:
    python -m semantic_compression.prepare_dictionary --interactive
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required:  pip install pyyaml")

REPO = Path(__file__).resolve().parent.parent          # R-D-concepts/
SC = "semantic_compression"
BUILDS = REPO / SC / "builds"

SIZES = ["char-2", "char-3", "char-4"]
STRATEGIES = ["frequency", "bytes_saved"]

# Source presets -> a corpus entry + the file the build needs to exist.
SOURCE_PRESETS = {
    "transcripts": (
        {"type": "transcripts", "precomputed": f"{SC}/data/word_frequencies.txt",
         "weight": 1.0, "license": "per-channel derived stats (counts only)"},
        REPO / SC / "data" / "word_frequencies.txt"),
    "books": (
        {"type": "books", "path": "Resources/books", "include": "*.txt",
         "weight": 1.0, "license": "public domain / user-supplied"},
        REPO / "Resources" / "books"),
    "web": (
        {"type": "web_structure", "precomputed": f"{SC}/data/web_terms_frequencies.txt",
         "weight": 1.0, "license": "browser web dictionary (structure tokens)"},
        REPO / SC / "data" / "web_terms_frequencies.txt"),
}

BAR = "=" * 70


def step(n: int, title: str) -> None:
    print(f"\n{BAR}\n  STEP {n} -- {title}\n{BAR}")


def info(msg: str) -> None:
    print(f"   {msg}")


def ask(prompt: str, default: str) -> str:
    got = input(f"   {prompt} [{default}]: ").strip()
    return got or default


# --------------------------------------------------------------------------- #
def interactive_fill(args: argparse.Namespace) -> argparse.Namespace:
    print(BAR + "\n  ELO Dictionary -- guided build\n" + BAR)
    args.name = ask("Build name (-> db/builds/<name>/)", args.name or "my-dict")
    args.size = ask(f"Size {SIZES} (char-3 on-device, char-4 LLM)", args.size or "char-4")
    args.sources = ask("Sources (comma): transcripts,books,web", args.sources or "transcripts,books")
    args.strategy = ask(f"Strategy {STRATEGIES}", args.strategy or "frequency")
    args.facets = ask("Build facets + meta? (y/n)", "y" if args.facets else "y").lower().startswith("y")
    args.purpose = ask("Purpose (one line)", args.purpose or f"{args.name} dictionary")
    yn = ask("Run the build now? (y/n)", "y" if args.build else "n")
    args.build = yn.lower().startswith("y")
    if args.build:
        args.verify = ask("Verify after build? (y/n)", "y").lower().startswith("y")
    return args


def build_spec(args: argparse.Namespace) -> dict:
    corpus, missing = [], []
    for key in [s.strip() for s in args.sources.split(",") if s.strip()]:
        if key not in SOURCE_PRESETS:
            sys.exit(f"unknown source '{key}'. choices: {list(SOURCE_PRESETS)}")
        entry, path = SOURCE_PRESETS[key]
        corpus.append(entry)
        if not path.exists():
            missing.append(f"{key} -> {path}")
    if missing:
        print("   WARNING: source file(s) not found (build will fail):")
        for m in missing:
            print(f"     - {m}")
    return {
        "meta": {
            "name": args.name,
            "version": "1.0.0",
            "author": args.author,
            "status": "staged",
            "purpose": args.purpose or f"{args.name} dictionary",
        },
        "build": {
            "size": args.size,
            "select_strategy": args.strategy,
            "min_freq": args.min_freq,
            "tier1_word_reserve": args.tier1_reserve,
            "with_facets": bool(args.facets),
        },
        "corpus": corpus,
        "eval": {"held_out_books": []},
        "instructions": (
            "Scaffolded by prepare_dictionary.py. Density is codec-driven "
            "(implicit-whitespace + caps); do not bake variants into the dict. "
            "char-3 caps ~83k -- use char-4 + the `full` profile cut for ~262k."
        ),
    }


def run(cmd: list[str]) -> int:
    print(f"   $ {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(REPO))


def summarize(name: str) -> None:
    import json
    mpath = REPO / SC / "db" / "builds" / name / "manifest.json"
    if not mpath.exists():
        info("(no manifest yet -- run with --build)")
        return
    m = json.loads(mpath.read_text(encoding="utf-8"))
    info(f"entries:       {m.get('entries', '?'):,}" if isinstance(m.get('entries'), int) else f"entries: {m.get('entries')}")
    info(f"size:          {m.get('size', '?')}")
    fp = m.get("corpus_fingerprint") or m.get("fingerprint") or "?"
    info(f"corpus fp:     {str(fp)[:16]}...")
    info(f"package:       {mpath.parent}")


# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description="Guided ELO dictionary build.")
    p.add_argument("--name")
    p.add_argument("--size", choices=SIZES, default=None)
    p.add_argument("--sources", help="comma list of: " + ",".join(SOURCE_PRESETS))
    p.add_argument("--strategy", choices=STRATEGIES, default="frequency")
    p.add_argument("--facets", action="store_true", help="build facets + meta.db")
    p.add_argument("--min-freq", type=int, default=1, dest="min_freq")
    p.add_argument("--tier1-reserve", type=int, default=1024, dest="tier1_reserve")
    p.add_argument("--author", default="Paul (4waymedia)")
    p.add_argument("--purpose", default=None)
    p.add_argument("--interactive", action="store_true")
    p.add_argument("--build", action="store_true", help="run build_from_spec")
    p.add_argument("--verify", action="store_true", help="verify_lossless + verify_facets")
    args = p.parse_args()

    if args.interactive:
        args = interactive_fill(args)
    if not args.name or not args.size or not args.sources:
        p.error("need --name, --size and --sources (or use --interactive)")

    # STEP 1 -- PREPARE
    step(1, "PREPARE  (scaffold the build spec)")
    spec = build_spec(args)
    BUILDS.mkdir(parents=True, exist_ok=True)
    spec_path = BUILDS / f"{args.name}.yaml"
    spec_rel = f"{SC}/builds/{args.name}.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False, width=88), encoding="utf-8")
    info(f"size={args.size}  strategy={args.strategy}  facets={bool(args.facets)}")
    info(f"sources={[c.get('type') for c in spec['corpus']]}")
    info(f"wrote spec -> {spec_rel}")
    if args.size != "char-4" and args.name and "262" in (args.purpose or ""):
        info("note: ~262k needs char-4 + the `full` profile cut (char-3 caps ~83k).")

    # STEP 2 -- BUILD
    step(2, "BUILD  (build_from_spec -> db/builds/<name>/)")
    if args.build:
        rc = run([sys.executable, "-m", "semantic_compression.build_from_spec", spec_rel])
        if rc != 0:
            sys.exit(f"build failed (exit {rc}).")
    else:
        info("skipped (add --build). To build now:")
        info(f"python -m semantic_compression.build_from_spec {spec_rel}")

    # STEP 3 -- VERIFY
    step(3, "VERIFY  (byte-exact round-trip + facets)")
    lmdb = f"{SC}/db/builds/{args.name}/dictionary.lmdb"
    if args.verify and args.build:
        run([sys.executable, f"{SC}/verify_lossless.py"])
        run([sys.executable, f"{SC}/verify_facets.py", "--db", lmdb])
    else:
        info("skipped (add --verify with --build). To verify:")
        info(f"python {SC}/verify_lossless.py")
        info(f"python {SC}/verify_facets.py --db {lmdb}")

    # STEP 4 -- REPORT
    step(4, "REPORT  (summary + next steps)")
    summarize(args.name)
    print()
    info("Next:")
    info(f"  score on held-out:  python -m {SC}.bench_dict_efficiency report --source books")
    info(f"  adopt/stamp:        python {SC}/stamp_meta.py --db {lmdb} --status frozen")
    info("  lock only when a model is trained on this exact fingerprint.")
    print(BAR)


if __name__ == "__main__":
    main()
