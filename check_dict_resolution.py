"""
check_dict_resolution.py -- the gate that keeps ONE answer to "which dictionary?"

RULING (integration lane, 2026-08-28) step 3: *every consumer resolves through the
package; nobody hardcodes a .lmdb path; nobody reads a deprecated env var. Advisory
for one release, then blocking. Without this the four private paths grow back.*

They grew back once already. The survey found four private implementations of one
missing function, two spellings of one env var, and every consumer that resolved at
all landing on fingerprint 9a77e623 -- a dictionary that matches NO build package in
the index, wearing release label v1.2.0. Nothing failed. Suites stayed green. That is
the failure mode this gate exists to make loud.

WHAT IS A VIOLATION

  V1  a hardcoded '<something>.lmdb' path in consumer code
  V2  a read of ELO_DICT_DB / ELO_DICT_LMDB (deprecated; one spelling is ELO_DICT)

WHAT IS NOT

  * the dictionary lane's OWN build/publish tooling -- it must address builds by path
    to build them at all
  * tests that construct a throwaway LMDB in tmp_path
  * documentation and specs, which need to name paths to explain them
  * resolve.py itself, which is the one legal place the knowledge lives

    python check_dict_resolution.py            # advisory, always exit 0
    python check_dict_resolution.py --strict   # blocking, exit 1 on any violation
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

SC = Path(__file__).resolve().parent
ROOT = SC.parent

# Consumer lanes -- the ones that must go through the resolver.
CONSUMERS = [
    "04-Verbalizer", "05-ExtractionPipeline", "06-RecalEngine", "07-ContextAssembly",
    "08-MCP-ToolInterface", "09-Reflection", "10-ELO-Integration", "ELO-Writer",
    "ELO-Notes", "Memory",
]

# Files exempt because owning the path IS their job.
EXEMPT_NAMES = {
    "resolve.py",                 # the one legal home for this knowledge
    "dictionary_standard.py",     # writes the pointer
    "dictionary_info.py",         # inspects an artifact it is handed
    "publish_dictionary.py", "build_assets.py", "build_from_spec.py",
    "build_dictionary.py", "dictionary_builder.py", "build_suite.py", "stamp_meta.py",
    # build tooling, also mirrored into packages/ -- it must address builds by path
    "dictionary_builder_v03.py", "facet_builder.py", "verify_lossless.py",
    "epa_match.py", "meta_builder.py", "vfacet_builder.py", "wordclass_builder.py",
    "coverage_census.py", "export_browser_assets.py", "export_browser_vocab.py",
    "check_dict_resolution.py",   # this file
}
EXEMPT_DIR_PARTS = {"tests", "test", "node_modules", ".venv", "__pycache__",
                    "docs", "handoffs", "dist", "build"}

# FALSE POSITIVES -- a different artifact that merely has "dictionary" in its name.
# `base.dictionary.lmdb` is the EloAI x L-SDF **code-ontology** dictionary: compiled
# from a locked base.dictionary.json, ELO IDs as classifiers, its own fingerprint
# discipline. It is not the System-1 dictionary and must NOT resolve through the
# System-1 standard. Checked before assuming; the regex matched on the word alone.
EXEMPT_PATH_SUBSTRINGS = ("eloai_lsdf/dictionary.py", "eloai_lsdf\\dictionary.py",
                          "base.dictionary",
                          # a MANIFEST TABLE of artifact names + their ship class
                          # ("dictionary.lmdb", "BUILD", "...must NOT ship in a wheel").
                          # It names the artifact to classify it; it never opens one.
                          "audit_package_data.py")

# V1 is scoped to DICTIONARY lmdbs. An earlier pass matched any '*.lmdb' and flagged
# memory.lmdb / recal.lmdb / idx.lmdb / epa_substrate.lmdb -- 95 hits, mostly noise.
# Those are different artifacts with their own resolution stories; folding them in here
# would make the gate unreadable and it would be ignored, which is worse than absent.
#
# epa_substrate.lmdb IS the same problem one artifact over (every lane hardcodes
# Memory/data/epa_substrate.lmdb). It is deliberately OUT OF SCOPE for this gate and
# recorded as adjacent work, not silently folded in.
V1 = re.compile(r"""["'][^"']*(?:dictionary|dict)[^"']*\.lmdb["']""", re.I)
V2 = re.compile(r"\bELO_DICT_(?:DB|LMDB)\b")
SUFFIXES = {".py", ".ts", ".js", ".toml", ".cfg", ".ini"}


def _exempt(p: Path) -> bool:
    if p.name in EXEMPT_NAMES:
        return True
    sp = str(p).replace("\\", "/")
    if any(s.replace("\\", "/") in sp for s in EXEMPT_PATH_SUBSTRINGS):
        return True
    parts = {q.lower() for q in p.parts}
    if parts & EXEMPT_DIR_PARTS:
        return True
    return p.name.startswith("test_") or p.name.endswith("_test.py")


def _string_literal_lines(text: str) -> set:
    """Line numbers inside Python string literals (docstrings included).

    Needed because a docstring EXPLAINING the old behaviour is documentation, not a
    violation -- and after the repoint most remaining hits were exactly that: this
    gate flagging the comments that describe what it caused to be fixed, including a
    docstring quoting the very line it replaced. A gate that cries wolf at its own
    fix notes gets switched off, so it has to tell code from prose."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    # ONLY bare string EXPRESSIONS (docstrings / prose blocks). A first version
    # skipped every ast.Constant str and reported 0 violations -- which is exactly
    # wrong, because a hardcoded path IS a string constant. It would have suppressed
    # the entire V1 class and reported the codebase clean. Caught by re-testing a
    # known-bad line instead of trusting the improvement.
    lines: set = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", start)
            if start:
                lines.update(range(start, (end or start) + 1))
    return lines


def scan(roots: list[Path]) -> list[dict]:
    out: list[dict] = []
    for r in roots:
        if not r.exists():
            continue
        for f in r.rglob("*"):
            if not f.is_file() or f.suffix not in SUFFIXES or _exempt(f):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            skip = _string_literal_lines(text) if f.suffix == ".py" else set()
            for i, line in enumerate(text.splitlines(), 1):
                s = line.strip()
                if s.startswith("#") or s.startswith("//") or s.startswith("*"):
                    continue          # a comment naming a path is documentation
                if i in skip:
                    continue          # inside a string literal -- prose, not code
                if V1.search(line):
                    out.append({"kind": "V1", "file": f, "line": i, "text": s[:100]})
                if V2.search(line):
                    out.append({"kind": "V2", "file": f, "line": i, "text": s[:100]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="dictionary-resolution gate")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 on any violation (blocking mode)")
    ap.add_argument("--all", action="store_true",
                    help="also scan semantic_compression (build tooling; noisy)")
    a = ap.parse_args()

    roots = [ROOT / c for c in CONSUMERS] + [ROOT / "packages"]
    if a.all:
        roots.append(SC)
    hits = scan(roots)

    print(f"dictionary-resolution gate  --  {len(hits)} violation(s)"
          f"{' [STRICT]' if a.strict else ' [advisory]'}")
    if hits:
        by_file: dict = {}
        for h in hits:
            by_file.setdefault(h["file"], []).append(h)
        for f, hs in sorted(by_file.items()):
            try:
                rel = f.relative_to(ROOT)
            except ValueError:
                rel = f
            print(f"\n  {rel}")
            for h in hs:
                print(f"    {h['kind']} line {h['line']}: {h['text']}")
        print("\n  V1 = hardcoded .lmdb path   V2 = deprecated env var")
        print("  FIX -- replace with the one resolver:")
        print("    from compression_dictionary import resolve_dictionary")
        print("    d = resolve_dictionary(require=('wordclass',))   # raises if absent")
        print("    d.path  d.fingerprint  d.build")
    else:
        print("  clean -- every consumer resolves through the package")

    if a.strict and hits:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
