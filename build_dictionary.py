"""
build_dictionary.py -- ONE command: build a dictionary AND its declared asset suite.

A dictionary is a shipped product. This is the single entrypoint that turns a build
spec into a finished, product-ready package: it runs the CORE build
(`build_from_spec`: dictionary + LLM profile cuts + the suite's facets/meta), then the
ASSET CASCADE (`build_assets`: epa → meta_layer2 → vectors → browser → stamp → verify)
for whatever `build.suite` declares — so `epa` and the browser export are no longer
separate steps you must remember. The two underlying stages stay independently
runnable; this just chains them from one declaration.

    python -m semantic_compression.build_dictionary semantic_compression/builds/<name>.yaml
    python -m semantic_compression.build_dictionary builds/<name>.yaml --device cuda
    python -m semantic_compression.build_dictionary builds/<name>.yaml --dry-run   # preview only

Flags after the spec are passed through to the cascade: --device, --force,
--browser-out, --release, --status (see build_assets.py).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

from semantic_compression import build_suite

SC = Path(__file__).resolve().parent          # semantic_compression/
ROOT = SC.parent                              # R-D-concepts/
PY = sys.executable


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("spec", type=Path)
    ap.add_argument("--dry-run", action="store_true",
                    help="preview the suite + cascade plan; build nothing")
    ap.add_argument("--device", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--browser-out", default=None)
    ap.add_argument("--release", default=None)
    ap.add_argument("--status", default=None)
    a = ap.parse_args()

    spec_path = a.spec.resolve()
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    name = spec["meta"]["name"]
    pkg = ROOT / "semantic_compression" / "db" / "builds" / name

    # Show the resolved suite up front (fails fast on a bad declaration).
    plan = build_suite.resolve_suite(spec, repo_root=ROOT)
    print(f"=== build_dictionary '{name}'  preset={spec.get('build',{}).get('suite','standard')} ===")
    print(build_suite.format_plan(plan))
    print()

    cascade = [PY, str(SC / "build_assets.py"), str(pkg)]
    for flag, val in (("--device", a.device), ("--browser-out", a.browser_out),
                      ("--release", a.release), ("--status", a.status)):
        if val:
            cascade += [flag, val]
    if a.force:
        cascade.append("--force")

    if a.dry_run:
        if not pkg.exists():
            print("[dry-run] core build would run (build_from_spec); package does not "
                  "exist yet, so the cascade preview is unavailable until it is built.")
            return
        print("[dry-run] core build would run; cascade plan:")
        subprocess.run(cascade + ["--dry-run"], cwd=str(SC))
        return

    # 1) CORE build (build_from_spec resolves the suite + gates facets/meta).
    print(">>> core build (build_from_spec)")
    r = subprocess.run([PY, "-m", "semantic_compression.build_from_spec", str(spec_path)],
                       cwd=str(ROOT))
    if r.returncode != 0:
        sys.exit(f"core build failed (exit {r.returncode})")

    # 2) ASSET CASCADE (build_assets derives the rest of the declared suite).
    print("\n>>> asset cascade (build_assets)")
    r = subprocess.run(cascade, cwd=str(SC))
    if r.returncode != 0:
        sys.exit(f"asset cascade failed (exit {r.returncode})")
    print(f"\n[done] {name}: core + declared suite built -> {pkg}")


if __name__ == "__main__":
    main()
